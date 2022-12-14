import numpy as np
from pymovis.utils import npconst

"""
Functions that convert between different rotation representations.

Glossary:
- A: Axis angle
- E: Euler angles
- R: Rotation matrix
- R6: 6D rotation vector [Zhou et al. 2018]
- Q: Quaternion (order in (w, x, y, z), where w is real value)
- v: Vector
- p: Position

TODO: Refactor code & Synchronize with torchmotion.py
"""

def normalize(x, axis=-1, eps=1e-8):
    res = x / (np.linalg.norm(x, axis=axis, keepdims=True) + eps)
    return res

class R:
    @staticmethod
    def fk(R, root_p, skeleton):
        """
        :param R: (..., N, 3, 3)
        :param root_p: (..., 3)
        :param bone_offset: (N, 3)
        :param parents: (N,)
        """
        bone_offsets, parents = skeleton.get_bone_offsets(), skeleton.parent_idx
        global_R, global_p = [R[..., 0, :, :]], [root_p]
        for i in range(1, len(parents)):
            global_R.append(np.matmul(global_R[parents[i]], R[..., i, :, :]))
            global_p.append(np.matmul(global_R[parents[i]], bone_offsets[i]) + global_p[parents[i]])
        
        global_R = np.stack(global_R, axis=-3) # (..., N, 3, 3)
        global_p = np.stack(global_p, axis=-2) # (..., N, 3)
        return global_R, global_p
    
    @staticmethod
    def from_E(E, order, radians=True):
        """
        :param E: (..., 3)
        """
        if E.shape[-1] != 3:
            raise ValueError(f"E.shape[-1] = {E.shape[-1]} != 3")
        
        if not radians:
            E = np.deg2rad(E)

        axis_map = {
            "x": npconst.X(),
            "y": npconst.Y(),
            "z": npconst.Z(),
        }

        R0 = R.from_A(E[..., 0], axis=axis_map[order[0]])
        R1 = R.from_A(E[..., 1], axis=axis_map[order[1]])
        R2 = R.from_A(E[..., 2], axis=axis_map[order[2]])
        return np.matmul(R0, np.matmul(R1, R2))
    
    @staticmethod
    def from_A(angle, axis):
        """
        :param angle: (..., N)
        :param axis:  (..., 3)
        """
        if axis.shape[-1] != 3:
            raise ValueError(f"axis.shape[-1] = {axis.shape[-1]} != 3")

        a0, a1, a2     = axis[..., 0], axis[..., 1], axis[..., 2]
        zero           = np.zeros_like(a0)
        skew_symmetric = np.stack([zero, -a2, a1,
                                    a2, zero, -a0,
                                    -a1, a0, zero], axis=-1).reshape(*angle.shape[:-1], 3, 3) # (..., 3, 3)
        I              = np.eye(3, dtype=np.float32)                                          # (3, 3)
        I              = np.tile(I, reps=[*angle.shape[:-1], 1, 1])                           # (..., 3, 3)
        sin            = np.sin(angle)[..., np.newaxis, np.newaxis]                           # (..., 1, 1)
        cos            = np.cos(angle)[..., np.newaxis, np.newaxis]                           # (..., 1, 1)
        return I + skew_symmetric * sin + np.matmul(skew_symmetric, skew_symmetric) * (1 - cos)

    @staticmethod
    def from_R6(r6: np.ndarray) -> np.ndarray:
        """
        :param r6: (..., 6)
        """
        if r6.shape[-1] != 6:
            raise ValueError(f"r6.shape[-1] = {r6.shape[-1]} != 6")
        
        x = normalize(r6[..., 0:3])
        y = normalize(r6[..., 3:6])
        z = np.cross(x, y, axis=-1)
        y = np.cross(z, x, axis=-1)
        return np.stack([x, y, z], axis=-2) # (..., 3, 3)

    @staticmethod
    def from_Q(q: np.ndarray) -> np.ndarray:
        """
        :param q: (..., 4)
        """
        if q.shape[-1] != 4:
            raise ValueError(f"q.shape[-1] = {q.shape[-1]} != 4")
        
        q = normalize(q, axis=-1)
        w, x, y, z = np.split(q, 4, axis=-1)
        row0 = np.stack([2*(w*w + x*x) - 1, 2*(x*y - w*z), 2*(x*z + w*y)], axis=-1)
        row1 = np.stack([2*(w*z + x*y), 2*(w*w + y*y) - 1, 2*(y*z - w*x)], axis=-1)
        row2 = np.stack([2*(x*z - w*y), 2*(w*x + y*z), 2*(w*w + z*z) - 1], axis=-1)
        return np.stack([row0, row1, row2], axis=-2) # (..., 3, 3)


    @staticmethod
    def inv(R):
        """
        :param R: (..., N, 3, 3)
        """
        if R.shape[-2:] != (3, 3):
            raise ValueError(f"R.shape[-2:] = {R.shape[-2:]} != (3, 3)")
        return np.transpose(R, axes=[*range(len(R.shape) - 2), -1, -2])

class R6:
    @staticmethod
    def from_R(R):
        """
        :param R: (..., 3, 3)
        """
        if R.shape[-2:] != (3, 3):
            raise ValueError(f"R.shape[-2:] = {R.shape[-2:]} != (3, 3)")
        r0 = R[..., 0, :]
        r1 = R[..., 1, :]
        return np.concatenate([r0, r1], axis=-1) # (..., 6)

    @staticmethod
    def from_Q(Q):
        """
        :param Q: (..., 4)
        """
        if Q.shape[-1] != 4:
            raise ValueError(f"Q.shape[-1] = {Q.shape[-1]} != 4")
        
        Q = normalize(Q, axis=-1)
        q0, q1, q2, q3 = Q[..., 0], Q[..., 1], Q[..., 2], Q[..., 3]

        r0 = np.stack([2*(q0*q0 + q1*q1) - 1, 2*(q1*q2 - q0*q3), 2*(q1*q3 + q0*q2)], axis=-1)
        r1 = np.stack([2*(q1*q2 + q0*q3), 2*(q0*q0 + q2*q2) - 1, 2*(q2*q3 - q0*q1)], axis=-1)
        return np.concatenate([r0, r1], axis=-1) # (..., 6)

class Q:
    @staticmethod
    def from_A(angle, axis):
        """
        Converts from and angle-axis representation to a quaternion representation

        :param angle: angles tensor (..., N)
        :param axis: axis tensor (..., 3)
        :return: quaternion tensor
        """
        if axis.shape[-1] != 3:
            raise ValueError(f"axis.shape[-1] = {axis.shape[-1]} != 3")

        axis = normalize(axis, axis=-1)
        a0, a1, a2 = axis[..., 0], axis[..., 1], axis[..., 2]
        cos = np.cos(angle / 2)[..., np.newaxis]
        sin = np.sin(angle / 2)[..., np.newaxis]

        return np.concatenate([cos, a0 * sin, a1 * sin, a2 * sin], axis=-1) # (..., 4)

    @staticmethod
    def from_E(E, order):
        axis = {
            'x': np.asarray([1, 0, 0], dtype=np.float32),
            'y': np.asarray([0, 1, 0], dtype=np.float32),
            'z': np.asarray([0, 0, 1], dtype=np.float32)}

        q0 = Q.from_A(E[..., 0], axis[order[0]])
        q1 = Q.from_A(E[..., 1], axis[order[1]])
        q2 = Q.from_A(E[..., 2], axis[order[2]])

        return Q.mul(q0, Q.mul(q1, q2))
    
    @staticmethod
    def mul(x, y):
        """
        Performs quaternion multiplication on arrays of quaternions

        :param x: tensor of quaternions of shape (..., Nb of joints, 4)
        :param y: tensor of quaternions of shape (..., Nb of joints, 4)
        :return: The resulting quaternions
        """
        x0, x1, x2, x3 = x[..., 0:1], x[..., 1:2], x[..., 2:3], x[..., 3:4]
        y0, y1, y2, y3 = y[..., 0:1], y[..., 1:2], y[..., 2:3], y[..., 3:4]

        res = np.concatenate([
            y0 * x0 - y1 * x1 - y2 * x2 - y3 * x3,
            y0 * x1 + y1 * x0 - y2 * x3 + y3 * x2,
            y0 * x2 + y1 * x3 + y2 * x0 - y3 * x1,
            y0 * x3 - y1 * x2 + y2 * x1 + y3 * x0], axis=-1)

        return res
    
    @staticmethod
    def inv(Q):
        """
        :param Q: (..., 4)
        """
        if Q.shape[-1] != 4:
            raise ValueError(f"Q.shape[-1] = {Q.shape[-1]} != 4")

        res = np.asarray([1, -1, -1, -1], dtype=np.float32) * Q
        return res