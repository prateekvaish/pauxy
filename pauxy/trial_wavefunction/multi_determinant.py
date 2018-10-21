import numpy
from pauxy.estimators.mixed import local_energy
from pauxy.estimators.greens_function import gab, gab_multi_det_full
from pauxy.utils.linalg import diagonalise_sorted
from pauxy.utils.io import read_fortran_complex_numbers

class MultiDeterminant(object):

    def __init__(self, system, cplx, trial, parallel=False, verbose=False):
        self.verbose = verbose
        if verbose:
            print ("# Parsing multi-determinant trial wavefunction input"
                   "options.")
        init_time = time.time()
        self.name = "multi_determinant"
        self.expansion = "multi_determinant"
        self.type = trial.get('type')
        self.ndets = trial.get('ndets', None)
        self.eigs = numpy.array([0.0])
        self.initial_wavefunction = trial.get('initial_wavefunction',
                                              'free_electron')
        self.bp_wfn = trial.get('bp_wfn', 'init')
        if cplx or self.type == 'GHF':
            self.trial_type = complex
        else:
            self.trial_type = float
        if self.type == 'UHF':
            nbasis = system.nbasis
        else:
            nbasis = 2 * system.nbasis
        self.GAB = numpy.zeros(shape=(self.ndets, self.ndets, nbasis, nbasis),
                               dtype=self.trial_type)
        self.weights = numpy.zeros(shape=(self.ndets, self.ndets),
                                   dtype=self.trial_type)
        # For debugging purposes.
        self.error = False
        if self.type == 'free_electron':
            (self.eigs, self.eigv) = diagonalise_sorted(system.T[0])
            psi = numpy.zeros(shape=(self.ndets, system.nbasis, system.ne))
            psi[:,:system.nup] = self.eigv[:,:system.nup]
            psi[:,system.nup:] = self.eigv[:,:system.ndown]
            self.psi = numpy.array([copy.deepcopy(psi) for i in range(0,self.ndets)])
            self.G = numpy.zeros(2, nbasis, nbasis)
            self.emin = sum(self.eigs[:system.nup]) + sum(self.eigs[:system.ndown])
            self.coeffs = numpy.ones(self.ndets)
        else:
            self.orbital_file = trial.get('orbitals', None)
            self.coeffs_file = trial.get('coefficients', None)
            if self.orbital_file is not None:
                self.from_ascii(system)
            elif system.orbs is not None:
                orbs = system.orbs.copy()
                nc = system.ncore
                nfv = system.nfv
                nb = system.nbasis
                if frozen_core:
                    orbs = orbs[:,nc:nb-nfv:nc:nb-nfv]
                    orbs_core = orbs[0,:,:nc]
                    Gcore, half = gab_mod(orbs_core, orbs_core)
                    self.Gcore = numpy.array([Gcore, Gcore])
                self.psi[:,:,:system.nup] = orbs[:,:,:system.nup].copy()
                self.psi[:,:,system.nup:] = orbs[:,:,system.nup:].copy()
                self.coeffs = system.coeffs
            else:
                print("Could not construct trial wavefunction.")
                self.error = True
            # Store the complex conjugate of the multi-determinant trial
            # wavefunction expansion coefficients for ease later.
            self.G = gab_multi_det_full(self.psi, self.psi,
                                        self.coeffs, self.coeffs,
                                        self.GAB, self.weights)
            self.trial = (local_energy_ghf_full(system, self.GAB,
                                                self.weights)[0].real)
        self.initialisation_time = time.time() - init_time
        if verbose:
            print ("# Finished setting up trial wavefunction.")

    def from_ascii(self, system):
        if self.verbose:
            print ("# Reading wavefunction from %s." % self.coeffs_file)
        self.coeffs = read_fortran_complex_numbers(self.coeffs_file)
        self.psi = numpy.zeros(shape=(self.ndets, system.nbasis, system.ne),
                               dtype=self.coeffs.dtype)
        orbitals = read_fortran_complex_numbers(self.orbital_file)
        start = 0
        skip = system.nbasis * system.ne
        end = skip
        for i in range(self.ndets):
            self.psi[i] = orbitals[start:end].reshape((nbasis, system.ne),
                                                      order='F')
            start = end
            end += skip

    def energy(self, system):
        if self.verbose:
            print ("# Computing trial energy.")
        (self.energy, self.e1b, self.e2b) = local_energy(system, self.G,
                                                         Ghalf=[self.gup_half,
                                                         self.gdown_half],
                                                         opt=True)
        if self.verbose:
            print ("# (E, E1B, E2B): (%13.8e, %13.8e, %13.8e)"
                   %(self.energy.real, self.e1b.real, self.e2b.real))
