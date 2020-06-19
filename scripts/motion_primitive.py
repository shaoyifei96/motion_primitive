from py_opt_control import min_time_bvp
import matplotlib.pyplot as plt
import sympy as sym
from scipy.special import factorial


class MotionPrimitive():
    """
    #WIP
    A motion primitive that defines a trajectory from a over a time T. Put functions that all MPs should have in here
    """

    def __init__(self, start_state, end_state, num_dims, max_state):
        """
        """
        self.start_state = start_state
        self.end_state = end_state
        self.num_dims = num_dims
        self.max_state = max_state
        self.control_space_q = int(start_state.shape[0]/num_dims)

    def get_state(self, t):
        """
        Given a time t, return the state of the motion primitive at that time. Will be specific to the subclass, so we raise an error if the subclass has not implemented it
        """
        raise NotImplementedError

    def plot_from_sampled_states(self, st, sp, sv, sa, sj):
        # Plot the state over time.
        fig, axes = plt.subplots(4, 1, sharex=True)
        for i in range(sp.shape[0]):
            for ax, s, l in zip(axes, [sp, sv, sa, sj], ('pos', 'vel', 'acc', 'jerk')):
                ax.plot(st, s[i, :])
                ax.set_ylabel(l)
        axes[3].set_xlabel('time')
        fig.suptitle('Full State over Time')


class PolynomialMotionPrimitive(MotionPrimitive):
    """
    A motion primitive constructed from polynomial coefficients
    """

    def __init__(self, start_state, end_state, num_dims, max_state, x_derivs=None):
        super().__init__(start_state, end_state, num_dims, max_state)
        self.polynomial_constructor(x_derivs)

    def get_state(self, t):
        pass

    def polynomial_constructor(self, x_derivs=None):
        """
        """
        if x_derivs is None:
            self.setup_bvp_meam_620_style()
        self.polys, self.traj_time = self.iteratively_solve_bvp_meam_620_style()

    def setup_bvp_meam_620_style(self):
        t = sym.symbols('t')
        self.poly_order = (self.control_space_q)*2-1  # why?
        x = np.squeeze(sym.Matrix(np.zeros((self.poly_order+1))))
        for i in range(self.poly_order+1):
            x[i] = t**(self.poly_order-i)  # Construct polynomial of the form [T**5,    T**4,   T**3, T**2, T, 1]

        self.x_derivs = []
        for i in range(self.control_space_q+1):
            self.x_derivs.append(sym.lambdify([t], x))
            x = sym.diff(x)  # iterate through all the derivatives

    def solve_bvp_meam_620_style(self, start_state, end_state, T):
        """
        Return polynomial coefficients for a trajectory from start_state ((n,) array) to end_state ((n,) array) in time interval [0,T]
        """
        A = np.zeros((self.poly_order+1, self.poly_order+1))
        for i in range(self.control_space_q):
            x = self.x_derivs[i]  # iterate through all the derivatives
            A[i, :] = x(0)  # x(ti) = start_state
            A[self.control_space_q+i, :] = x(T)  # x(tf) = end_state

        polys = np.zeros((self.num_dims, self.poly_order+1))
        b = np.zeros(self.control_space_q*2)
        for i in range(self.num_dims):  # Construct a separate polynomial for each dimension

            # vector of the form [start_state,end_state,start_state_dot,end_state_dot,...]
            b[:self.control_space_q] = start_state[i::self.num_dims]
            b[self.control_space_q:] = end_state[i::self.num_dims]
            poly = np.linalg.solve(A, b)

            polys[i, :] = poly

        return polys

    def iteratively_solve_bvp_meam_620_style(self):
        """
        Given a start and goal pt, iterate over solving the BVP until the input constraint is satisfied. TODO: only checking input constraint at start and end at the moment
        """
        # TODO maybe static method?
        # TODO make parameters
        dt = .2
        max_t = 1
        t = 0
        u_max = np.inf
        polys = None
        while u_max > self.max_state[self.control_space_q]:
            t += dt
            if t > max_t:
                # u = np.ones(self.num_dims)*np.inf
                polys = None
                t = np.inf
                break
            polys = self.solve_bvp_meam_620_style(self.start_state, self.end_state, t)
            # TODO this is only u(t), not necessarily max(u) from 0 to t which we would want, use critical points maybe?
            u_max = max(abs(np.sum(polys*self.x_derivs[-1](t), axis=1)))
            u_max = max(u_max, max(abs(np.sum(polys*self.x_derivs[-1](t/2), axis=1))))
            u_max = max(u_max, max(abs(np.sum(polys*self.x_derivs[-1](0), axis=1))))
        return polys, t

    def evaluate_polynomial_at_derivative(self, deriv_num, st):
        # TODO reuse this into get_state
        return np.vstack([np.array([np.polyval(np.pad(self.x_derivs[deriv_num](1), ((deriv_num), (0)), mode='constant')[:-deriv_num] * self.polys[j, :], i) for i in st]) for j in range(self.num_dims)])

    def plot(self):
        st = np.linspace(0, self.traj_time, 100)
        sp = np.vstack([np.array([np.polyval(self.polys[j, :], i) for i in st]) for j in range(self.num_dims)])
        sv = self.evaluate_polynomial_at_derivative(1, st)
        sa = self.evaluate_polynomial_at_derivative(2, st)
        sj = self.evaluate_polynomial_at_derivative(3, st)
        self.plot_from_sampled_states(st, sp, sv, sa, sj)


class JerksMotionPrimitive(MotionPrimitive):
    """
    A motion primitive constructed from a sequence of constant jerks
    """

    def __init__(self, start_state, end_state, num_dims, max_state):
        super().__init__(start_state, end_state, num_dims, max_state)
        self.jerks_constructor()

    def get_state(self, t):
        pass

    def jerks_constructor(self):
        """
        jerks_data = ([switch times],[jerk values]) 
        """
        self.switch_times, self.jerks = self.solve_bvp_min_time()

    def solve_bvp_min_time(self):
        """
        Solve the BVP for time optimal jerk control trajectories as in Beul ICUAS '17 https://github.com/jpaulos/opt_control 
        """
        # TODO staticmethod?

        # start point
        p0, v0, a0 = np.split(self.start_state, self.control_space_q)
        # end point
        p1, v1, a1 = np.split(self.end_state, self.control_space_q)

        # state and input limits
        v_max, a_max, j_max = self.max_state[1:1+self.control_space_q]
        v_min, a_min, j_min = -self.max_state[1:1+self.control_space_q]
        # call to optimization library
        (t, j) = min_time_bvp.min_time_bvp(p0, v0, a0, p1, v1, a1, v_min, v_max, a_min,
                                           a_max, j_min, j_max)

        return t, j

    def plot(self):
        p0, v0, a0 = np.split(self.start_state, self.control_space_q)
        st, sj, sa, sv, sp = min_time_bvp.sample_min_time_bvp(p0, v0, a0, self.switch_times, self.jerks, dt=0.001)
        self.plot_from_sampled_states(st, sp, sv, sa, sj)


if __name__ == "__main__":
    # mp = PolynomialMotionPrimitive([1, 2, 3, 4, 5])
    import numpy as np
    start_state = np.ones((6,))*.1
    end_state = np.zeros((6,))
    num_dims = 2
    max_state = np.ones((4,))*100
    mp = JerksMotionPrimitive(start_state, end_state, num_dims, max_state)
    mp.plot()

    mp = PolynomialMotionPrimitive(start_state, end_state, num_dims, max_state)
    mp.plot()

    plt.show()
