from motion_primitives_py.motion_primitive import *
import sympy as sym



class PolynomialMotionPrimitive(MotionPrimitive):
    """
    A motion primitive constructed from polynomial coefficients
    """

    def __init__(self, start_state, end_state, num_dims, max_state, 
                 subclass_specific_data={}):
        # Initialize class
        super().__init__(start_state, end_state, num_dims, max_state, 
                         subclass_specific_data)
        # TODO this code is duplicated
        if not self.subclass_specific_data.get('x_derivs'):
            self.x_derivs = self.get_dynamics_polynomials(self.control_space_q)
        else:
            self.x_derivs = self.subclass_specific_data.get('x_derivs')

        # Solve boundary value problem
        self.polys, traj_time = self.iteratively_solve_bvp_meam_620_style(
            self.start_state, self.end_state, self.num_dims, 
            self.max_state, self.x_derivs)
        if self.polys is not None:
            self.is_valid = True
            self.cost = traj_time

    @classmethod
    def from_dict(cls, dict, num_dims, max_state, subclass_specific_data={}):
        """
        Load a polynomial representation of a motion primitive from a dictionary 
        """
        mp = super().from_dict(dict, num_dims, max_state)
        if mp:
            mp.polys = np.array(dict["polys"])
            # TODO this code is duplicated
            if not mp.subclass_specific_data.get('x_derivs'):
                mp.x_derivs = mp.get_dynamics_polynomials(mp.control_space_q)
            else:
                mp.x_derivs = mp.subclass_specific_data.get('x_derivs')
        return mp

    def to_dict(self):
        """
        Write important attributes of motion primitive to a dictionary
        """
        dict = super().to_dict()
        if dict:
            dict["polys"] = self.polys.tolist()
        return dict

    def get_state(self, t):
        """
        Evaluate full state of a trajectory at a given time
        Input:
            t, numpy array of times to sample at
        Return:
            state, a numpy array of size (num_dims x control_space_q, len(t))
        """
        return np.vstack([self.evaluate_polynomial_at_derivative(i, [t]) for i in range(self.control_space_q)])

    def get_sampled_states(self, step_size=0.1):
        # TODO connect w/ get_state
        if self.is_valid:
            st = np.arange(self.cost, step=step_size)
            sp = np.vstack([np.array([np.polyval(self.polys[j, :], i) for i in st]) for j in range(self.num_dims)])
            sv = self.evaluate_polynomial_at_derivative(1, st)
            sa = self.evaluate_polynomial_at_derivative(2, st)
            if self.control_space_q >= 3:
                sj = self.evaluate_polynomial_at_derivative(3, st)
            else:
                sj = None
            return st, sp, sv, sa, sj
        else:
            return None, None, None, None, None

    def evaluate_polynomial_at_derivative(self, deriv_num, st):
        """
        Sample the specified derivative number of the polynomial trajectory at
        the specified times
        Input:
            deriv_num, order of derivative, scalar
            st, numpy array of times to sample
        Output:
            sampled, array of polynomial derivative evaluated at sample times
        """

        sampled = np.vstack([np.array([np.polyval(np.pad((self.x_derivs[deriv_num](1) * self.polys[j, :]),
                                                         ((deriv_num), (0)))[:self.polys.shape[1]], i) for i in st]) for j in range(self.num_dims)])

        return sampled

    @staticmethod
    def get_dynamics_polynomials(control_space_q, coefficients=None):
        """
        Returns an array of lambda functions that evaluate the derivatives of 
        a polynomial of specified order with coefficients all set to 1
        
        Example for polynomial order 5:
        time_derivatives[0] = lambda t: [t**5, t**4, t**3, t**2, t, 1]
        time_derivatives[1] = lambda t: [5*t**4, 4*t**3, 3*t**2, 2*t, 1, 0]
        time_derivatives[2] = lambda t: [20*t**3, 12*t**2, 6*t, 2, 0, 0]
        time_derivatives[3] = lambda t: [60*t**2, 24*t, 6, 0, 0, 0]
        
        Input:
            control_space_q, derivative of configuration which is control input
                infer order from this using equation: 2 * control_space_q - 1,
                number of derivatives returned will be control_space_q + 1

        Output:
            time_derivatives, an array of length (control_space_q + 1)
                represents the time derivatives of the specified polynomial with
                the ith element of the array representing the ith derivative
        """
        # construct polynomial of the form [T**5, T**4, T**3, T**2, T, 1]
        order = 2 * control_space_q - 1
        t = sym.symbols('t')
        x = np.squeeze(sym.Matrix([t**(order - i) for i in range(order + 1)]))

        # iterate through relevant derivatives and make function for each
        time_derivatives = []
        for i in range(control_space_q + 1):
            time_derivatives.append(sym.lambdify([t], x))
            x = sym.diff(x)  
        return time_derivatives

    @staticmethod
    def solve_bvp_meam_620_style(start_state, end_state, num_dims, x_derivs, T):
        """
        Return polynomial coefficients for a trajectory from start_state ((n,) array) to end_state ((n,) array) in time interval [0,T]
        The array of lambda functions created in get_dynamics_polynomials and the dimension of the configuration space are also required.
        """
        control_space_q = int(start_state.shape[0]/num_dims)
        poly_order = (control_space_q)*2-1
        A = np.zeros((poly_order+1, poly_order+1))
        for i in range(control_space_q):
            x = x_derivs[i]  # iterate through all the derivatives
            A[i, :] = x(0)  # x(ti) = start_state
            A[control_space_q+i, :] = x(T)  # x(tf) = end_state

        polys = np.zeros((num_dims, poly_order+1))
        b = np.zeros(control_space_q*2)
        for i in range(num_dims):  # Construct a separate polynomial for each dimension

            # vector of the form [start_state,end_state,start_state_dot,end_state_dot,...]
            b[:control_space_q] = start_state[i::num_dims]
            b[control_space_q:] = end_state[i::num_dims]
            poly = np.linalg.solve(A, b)

            polys[i, :] = poly

        return polys

    @staticmethod
    def iteratively_solve_bvp_meam_620_style(start_state, end_states, num_dims, max_state, x_derivs):
        """
        Given a start and goal pt, iterate over solving the BVP until the input constraint is satisfied-ish. TODO: only checking input constraint at start, middle, and end at the moment
        """
        # TODO make parameters
        dt = .1
        max_t = 1
        t = 0
        u_max = np.inf
        polys = None
        control_space_q = int(start_state.shape[0]/num_dims)
        while u_max > max_state[control_space_q]:
            t += dt
            if t > max_t:
                # u = np.ones(self.num_dims)*np.inf
                polys = None
                t = np.inf
                break
            polys = PolynomialMotionPrimitive.solve_bvp_meam_620_style(start_state, end_states, num_dims, x_derivs, t)
            # TODO this is only u(t), not necessarily max(u) from 0 to t which we would want, use critical points maybe?
            u_max = max(abs(np.sum(polys*x_derivs[-1](t), axis=1)))
            u_max = max(u_max, max(abs(np.sum(polys*x_derivs[-1](t/2), axis=1))))
            u_max = max(u_max, max(abs(np.sum(polys*x_derivs[-1](0), axis=1))))
        return polys, t


if __name__ == "__main__":
    # problem parameters
    num_dims = 2
    control_space_q = 3

    # setup problem
    start_state = np.zeros((num_dims * control_space_q,))
    end_state = np.random.rand(num_dims * control_space_q,)
    max_state = np.ones((num_dims * control_space_q,))*100

    # polynomial
    mp2 = PolynomialMotionPrimitive(start_state, end_state, num_dims, max_state)

    # save
    assert(mp2.is_valid)
    dictionary2 = mp2.to_dict()

    # reconstruct
    mp2 = PolynomialMotionPrimitive.from_dict(dictionary2, num_dims, max_state)

    # plot
    st, sp, sv, sa, sj = mp2.get_sampled_states()
    mp2.plot_from_sampled_states(st, sp, sv, sa, sj)
    plt.show()
