import cmath
import math
import numpy
import scipy.linalg
import sys
from pauxy.utils.linalg import exponentiate_matrix
from pauxy.walkers.single_det import SingleDetWalker
from pauxy.utils.linalg import reortho

class GenericContinuous(object):
    """Propagator for generic many-electron Hamiltonian.

    Uses continuous HS transformation for exponential of two body operator.

    Parameters
    ----------
    options : dict
        Propagator input options.
    qmc : :class:`pauxy.qmc.options.QMCOpts`
        QMC options.
    system : :class:`pauxy.system.System`
        System object.
    trial : :class:`pauxy.trial_wavefunctioin.Trial`
        Trial wavefunction object.
    verbose : bool
        If true print out more information during setup.
    """

    def __init__(self, system, trial, qmc, options={}, verbose=False):
        optimised = options.get('optimised', True)
        # Derived Attributes
        self.dt = qmc.dt
        self.sqrt_dt = qmc.dt**0.5
        self.isqrt_dt = 1j*self.sqrt_dt
        if trial.name == "MultiSlater":
            optimised = False
            self.mf_shift = (
                    self.construct_mean_field_shift_multi_det(system, trial)
                    )
        else:
            self.mf_shift = self.construct_mean_field_shift(system, trial)

        if verbose:
            print("# Absolute value of maximum component of mean field shift: "
                  "{:13.8e}.".format(numpy.max(numpy.abs(self.mf_shift))))
        # Mean field shifted one-body propagator
        self.construct_one_body_propagator(system, qmc.dt)
        # Constant core contribution modified by mean field shift.
        self.mf_core = system.ecore + 0.5*numpy.dot(self.mf_shift, self.mf_shift)
        self.nstblz = qmc.nstblz
        self.vbias = numpy.zeros(system.nfields, dtype=numpy.complex128)
        if optimised:
            self.construct_force_bias = self.construct_force_bias_fast
            self.construct_VHS = self.construct_VHS_fast
        else:
            if trial.name == "MultiSlater":
                self.construct_force_bias = self.construct_force_bias_multi_det
            else:
                self.construct_force_bias = self.construct_force_bias_slow
            self.construct_VHS = self.construct_VHS_slow
        self.ebound = (2.0/self.dt)**0.5
        self.mean_local_energy = 0
        if verbose:
            print("# Finished setting up Generic propagator.")

    def construct_mean_field_shift(self, system, trial):
        if system.sparse:
            mf_shift = 1j*trial.G[0].ravel()*system.hs_pot
            mf_shift += 1j*trial.G[1].ravel()*system.hs_pot
        else:
            mf_shift = 1j*numpy.einsum('lpq,spq->l',
                                       system.hs_pot,
                                       trial.G)
        return mf_shift

    def construct_mean_field_shift_multi_det(self, system, trial):
        mf_shift = [trial.contract_one_body(Vpq) for Vpq in system.hs_pot]
        mf_shift = 1j*numpy.array(mf_shift)
        return mf_shift

    def construct_one_body_propagator(self, system, dt):
        """Construct mean-field shifted one-body propagator.

        Parameters
        ----------
        dt : float
            Timestep.
        chol_vecs : :class:`numpy.ndarray`
            Cholesky vectors.
        h1e_mod : :class:`numpy.ndarray`
            One-body operator including factor from factorising two-body
            Hamiltonian.
        """
        if system.sparse:
            nb = system.nbasis
            shift = 1j*system.hs_pot.dot(self.mf_shift).reshape(nb,nb)
        else:
            shift = 1j*numpy.einsum('l,lpq->pq',
                                    self.mf_shift,
                                    system.hs_pot)
        H1 = system.h1e_mod - numpy.array([shift,shift])
        self.BH1 = numpy.array([scipy.linalg.expm(-0.5*dt*H1[0]),
                                scipy.linalg.expm(-0.5*dt*H1[1])])

    def construct_force_bias_slow(self, system, walker, trial):
        """Compute optimal force bias.

        Uses explicit expression.

        Parameters
        ----------
        G: :class:`numpy.ndarray`
            Walker's Green's function.

        Returns
        -------
        xbar : :class:`numpy.ndarray`
            Force bias.
        """
        vbias = numpy.einsum('lpq,pq->l', system.hs_pot, walker.G[0])
        vbias += numpy.einsum('lpq,pq->l', system.hs_pot, walker.G[1])
        return - self.sqrt_dt * (1j*vbias-self.mf_shift)

    def construct_force_bias_fast(self, system, walker, trial):
        """Compute optimal force bias.

        Uses rotated Green's function.

        Parameters
        ----------
        Gmod : :class:`numpy.ndarray`
            Half-rotated walker's Green's function.

        Returns
        -------
        xbar : :class:`numpy.ndarray`
            Force bias.
        """
        G = walker.Gmod
        self.vbias = G[0].ravel() * system.rot_hs_pot[0]
        self.vbias += G[1].ravel() * system.rot_hs_pot[1]
        return - self.sqrt_dt * (1j*self.vbias-self.mf_shift)

    def construct_force_bias_multi_det(self, system, walker, trial):
        vbias = numpy.array([walker.contract_one_body(Vpq, trial)
                             for Vpq in system.hs_pot])
        return - self.sqrt_dt * (1j*vbias-self.mf_shift)

    def construct_VHS_slow(self, system, shifted):
        return self.isqrt_dt * numpy.einsum('l,lpq->pq',
                                            shifted,
                                            system.hs_pot)

    def construct_VHS_fast(self, system, xshifted):
        """Construct the one body potential from the HS transformation
        Parameters
        ----------
        system :
            system class
        xshifted : numpy array
            shifited auxiliary field
        Returns
        -------
        VHS : numpy array
            the HS potential
        """
        VHS = system.hs_pot.dot(xshifted)
        VHS = VHS.reshape(system.nbasis, system.nbasis)
        return  self.isqrt_dt * VHS

def construct_propagator_matrix_generic(system, BT2, config, dt, conjt=False):
    """Construct the full projector from a configuration of auxiliary fields.

    For use with generic system object.

    Parameters
    ----------
    system : class
        System class.
    BT2 : :class:`numpy.ndarray`
        One body propagator.
    config : numpy array
        Auxiliary field configuration.
    conjt : bool
        If true return Hermitian conjugate of matrix.

    Returns
    -------
    B : :class:`numpy.ndarray`
        Full propagator matrix.
    """
    VHS = 1j*dt**0.5*numpy.einsum('l,lpq->pq', config, system.chol_vecs)
    EXP_VHS = exponentiate_matrix(VHS)
    Bup = BT2[0].dot(EXP_VHS).dot(BT2[0])
    Bdown = BT2[1].dot(EXP_VHS).dot(BT2[1])

    if conjt:
        return [Bup.conj().T, Bdown.conj().T]
    else:
        return [Bup, Bdown]


# def back_propagate(system, psi, trial, nstblz, BT2, dt):
    # r"""Perform back propagation for RHF/UHF style wavefunction.

    # For use with generic system hamiltonian.

    # Parameters
    # ---------
    # system : system object in general.
        # Container for model input options.
    # psi : :class:`pauxy.walkers.Walkers` object
        # CPMC wavefunction.
    # trial : :class:`pauxy.trial_wavefunction.X' object
        # Trial wavefunction class.
    # nstblz : int
        # Number of steps between GS orthogonalisation.
    # BT2 : :class:`numpy.ndarray`
        # One body propagator.
    # dt : float
        # Timestep.

    # Returns
    # -------
    # psi_bp : list of :class:`pauxy.walker.Walker` objects
        # Back propagated list of walkers.
    # """
    # psi_bp = [SingleDetWalker({}, system, trial, index=w) for w in range(len(psi))]
    # nup = system.nup
    # for (iw, w) in enumerate(psi):
        # # propagators should be applied in reverse order
        # for (i, c) in enumerate(w.field_configs.get_block()[0][::-1]):
            # # could make this system specific to reduce need for multiple
            # # routines.
            # B = construct_propagator_matrix_generic(system, BT2, c, dt, True)
            # psi_bp[iw].phi[:,:nup] = B[0].dot(psi_bp[iw].phi[:,:nup])
            # psi_bp[iw].phi[:,nup:] = B[1].dot(psi_bp[iw].phi[:,nup:])
            # if i != 0 and i % nstblz == 0:
                # psi_bp[iw].reortho(trial)
    # return psi_bp

def back_propagate_generic(phi, configs, system, nstblz, BT2, dt, store=False):
    r"""Perform back propagation for RHF/UHF style wavefunction.

    For use with generic system hamiltonian.

    Parameters
    ---------
    system : system object in general.
        Container for model input options.
    psi : :class:`pauxy.walkers.Walkers` object
        CPMC wavefunction.
    trial : :class:`pauxy.trial_wavefunction.X' object
        Trial wavefunction class.
    nstblz : int
        Number of steps between GS orthogonalisation.
    BT2 : :class:`numpy.ndarray`
        One body propagator.
    dt : float
        Timestep.

    Returns
    -------
    psi_bp : list of :class:`pauxy.walker.Walker` objects
        Back propagated list of walkers.
    """
    nup = system.nup
    psi_store = []
    for (i, c) in enumerate(configs.get_block()[0][::-1]):
        B = construct_propagator_matrix_generic(system, BT2, c, dt, True)
        phi[:,:nup] = numpy.dot(B[0].conj().T, phi[:,:nup])
        phi[:,nup:] = numpy.dot(B[1].conj().T, phi[:,nup:])
        if i != 0 and i % nstblz == 0:
            (phi[:,:nup], R) = reortho(phi[:,:nup])
            (phi[:,nup:], R) = reortho(phi[:,nup:])
        if store:
            psi_store.append(phi.copy())

    return psi_store

def back_propagate_generic_bmat(system, psi, trial, nstblz):
    r"""Perform back propagation for RHF/UHF style wavefunction.
    """
    psi_bp = [SingleDetWalker({}, system, trial, index=w) for w in range(len(psi))]
    nup = system.nup
    for (iw, w) in enumerate(psi):
        # propagators should be applied in reverse order
        for (i, B) in enumerate(w.stack.stack[::-1]):
            # could make this system specific to reduce need for multiple
            # routines.
            psi_bp[iw].phi[:,:nup] = numpy.dot(B[0].conj().T,
                                               psi_bp[iw].phi[:,:nup])
            psi_bp[iw].phi[:,nup:] = numpy.dot(B[1].conj().T,
                                               psi_bp[iw].phi[:,nup:])
            if i != 0 and i % nstblz == 0:
                psi_bp[iw].reortho(trial)
    return psi_bp
