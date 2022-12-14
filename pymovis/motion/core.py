from __future__ import annotations
import numpy as np
import copy
import glm

from pymovis.utils import npconst
from pymovis.motion.ops import npmotion

from pymovis.vis.render import Render

class Joint:
    """
    Joint of a skeleton

    Attributes:
        name   (str):        Name of the joint
        offset (np.ndarray): Offset of the joint from its parent
    """
    def __init__(
        self,
        name  : str,
        offset: np.ndarray=npconst.P_ZERO()
    ):
        self.name   = name
        self.offset = offset

class Skeleton:
    """
    Hierarchical structure of joints

    Attributes:
        joints      (list[Joint])    : List of joints
        v_up        (np.ndarray)     : Up vector of the skeleton
        v_forward   (np.ndarray)     : Forward vector of the skeleton
        parent_id   (list[int])      : List of parent ids
        children_id (list[list[int]]): List of children ids
        id_by_name  (dict[str, int]) : Dictionary of joint ids by name
    """
    def __init__(
        self,
        joints: list[Joint]=[],
        v_up: np.ndarray=npconst.UP(),
        v_forward: np.ndarray=npconst.FORWARD(),
    ):
        assert v_up.shape == (3,), f"v_up.shape = {v_up.shape}"
        assert v_forward.shape == (3,), f"v_forward.shape = {v_forward.shape}"

        self.joints = joints

        self.v_up = v_up
        self.v_forward = v_forward
        
        self.parent_idx = []
        self.children_idx = []
        self.idx_by_name = {}
    
    @property
    def num_joints(self):
        return len(self.joints)
    
    @property
    def effector_idx(self):
        res = []
        for i in range(len(self.joints)):
            if len(self.children_idx[i]) == 0:
                res.append(i)
        return res

    def add_joint(self, joint_name, parent_idx=None):
        joint_idx = len(self.joints)

        if parent_idx is None:
            assert len(self.joints) == 0, "Only one root joint is allowed"
            self.parent_idx.append(-1)
            self.children_idx.append(list())
        else:
            self.parent_idx.append(parent_idx)
            self.children_idx[parent_idx].append(joint_idx)

        joint = Joint(joint_name)
        self.idx_by_name[joint_name] = len(self.joints)
        self.joints.append(joint)
        self.children_idx.append(list())
    
    def get_bone_offsets(self):
        return np.stack([joint.offset for joint in self.joints], axis=0)
    
    def get_joint_by_name(self, name):
        return self.joints[self.idx_by_name[name]]

class Pose:
    """
    Represents a pose of a skeleton.
    It contains the local rotation matrices of each joint and the root position.

    Attributes:
        skeleton (Skeleton)     : The skeleton that this pose belongs to.
        local_R  (numpy.ndarray): The local rotation matrices of the joints.
        root_p   (numpy.ndarray): The root position.
    """
    def __init__(
        self,
        skeleton: Skeleton,
        local_R : np.ndarray,
        root_p  : np.ndarray=npconst.P_ZERO(),
    ):
        assert local_R.shape == (skeleton.num_joints, 3, 3), f"local_R.shape = {local_R.shape}"
        assert root_p.shape == (3,), f"root_p.shape = {root_p.shape}"

        self.skeleton = skeleton
        self.local_R = local_R
        self.root_p = root_p
    
    @classmethod
    def from_bvh(cls, skeleton, local_E, order, root_p):
        local_R = npmotion.R.from_E(local_E, order, radians=False)
        return cls(skeleton, local_R, root_p)
    
    @classmethod
    def from_numpy(cls, skeleton, local_R, root_p):
        return cls(skeleton, local_R, root_p)

    @classmethod
    def from_torch(cls, skeleton, local_R, root_p):
        return cls(skeleton, local_R.cpu().numpy(), root_p.cpu().numpy())

    """ Base position and directions (on xz plane, equivalent to horizontal plane) """
    @property
    def base(self):
        return self.root_p * npconst.XZ()
    
    @property
    def forward(self):
        return npmotion.normalize((self.local_R[0] @ self.skeleton.v_forward) * npconst.XZ())
    
    @property
    def up(self):
        return npconst.UP()
    
    @property
    def left(self):
        return npmotion.normalize(np.cross(self.up, self.forward))

    def draw(self, albedo=glm.vec3(1.0, 0.0, 0.0)):
        if not hasattr(self, "joint_sphere"):
            self.joint_sphere = Render.sphere(0.05)
            self.joint_bone   = Render.cylinder(0.03, 1.0)

        _, global_p = npmotion.R.fk(self.local_R, self.root_p, self.skeleton)
        for i in range(self.skeleton.num_joints):
            self.joint_sphere.set_position(global_p[i]).set_material(albedo=albedo).draw()

            if i != 0:
                parent_pos = global_p[self.skeleton.parent_idx[i]]

                center = glm.vec3((parent_pos + global_p[i]) / 2)
                dist = np.linalg.norm(parent_pos - global_p[i])
                dir = glm.vec3((global_p[i] - parent_pos) / dist)

                axis = glm.cross(glm.vec3(0, 1, 0), dir)
                angle = glm.acos(glm.dot(glm.vec3(0, 1, 0), dir))
                rotation = glm.rotate(glm.mat4(1.0), angle, axis)
                
                self.joint_bone.set_position(center).set_orientation(rotation).set_scale(glm.vec3(1.0, dist, 1.0)).set_material(albedo=albedo).draw()

    """ IK functions """
    def two_bone_ik(self, base_idx, effector_idx, target_p, eps=1e-8):
        if self.skeleton.parent_idx[self.skeleton.parent_idx[effector_idx]] != base_idx:
            raise ValueError(f"{base_idx} and {effector_idx} are not in a two bone IK hierarchy")

        global_R, global_p = npmotion.R.fk(self.local_R, self.root_p, self.skeleton)

        a = global_p[base_idx]
        b = global_p[self.skeleton.parent_idx[effector_idx]]
        c = global_p[effector_idx]

        global_a_R = global_R[base_idx]
        global_b_R = global_R[self.skeleton.parent_idx[effector_idx]]

        lab = np.linalg.norm(b - a)
        lcb = np.linalg.norm(b - c)
        lat = np.clip(np.linalg.norm(target_p - a), eps, lab + lcb - eps)

        ac_ab_0 = np.arccos(np.clip(np.dot(npmotion.normalize(c - a), npmotion.normalize(b - a)), -1, 1))
        ba_bc_0 = np.arccos(np.clip(np.dot(npmotion.normalize(a - b), npmotion.normalize(c - b)), -1, 1))
        ac_at_0 = np.arccos(np.clip(np.dot(npmotion.normalize(c - a), npmotion.normalize(target_p - a)), -1, 1))

        ac_ab_1 = np.arccos(np.clip((lcb*lcb - lab*lab - lat*lat) / (-2*lab*lat), -1, 1))
        ba_bc_1 = np.arccos(np.clip((lat*lat - lab*lab - lcb*lcb) / (-2*lab*lcb), -1, 1))

        d = global_b_R @ npconst.Z()
        axis_0 = npmotion.normalize(np.cross(c - a, d))
        axis_1 = npmotion.normalize(np.cross(c - a, target_p - a))

        r0 = npmotion.R.from_A(ac_ab_1 - ac_ab_0, npmotion.R.inv(global_a_R) @ axis_0)
        r1 = npmotion.R.from_A(ba_bc_1 - ba_bc_0, npmotion.R.inv(global_b_R) @ axis_0)
        r2 = npmotion.R.from_A(ac_at_0, npmotion.R.inv(global_a_R) @ axis_1)

        self.local_R[base_idx] = self.local_R[base_idx] @ r0 @ r2
        self.local_R[self.skeleton.parent_idx[effector_idx]] = self.local_R[self.skeleton.parent_idx[effector_idx]] @ r1

class Motion:
    """
    Motion class that contains the skeleton and its sequence of poses.

    Attributes:
        name      (str)       : The name of the motion.
        skeleton  (Skeleton)  : The skeleton that this motion belongs to.
        poses     (list[Pose]): The sequence of poses.
        fps       (float)     : The number of frames per second.
        frametime (float)     : The time between two frames.
    """
    def __init__(
        self,
        name: str,
        skeleton: Skeleton,
        poses: list[Pose],
        global_v: np.ndarray = None,
        fps: float=30.0,
    ):
        self.name = name
        self.skeleton = skeleton
        self.poses = poses
        self.fps = fps
        self.frametime = 1.0 / fps

        # local rotations and root positions are stored separately from the poses
        # to make the computation faster
        self.local_R = np.stack([pose.local_R for pose in poses], axis=0)
        self.root_p  = np.stack([pose.root_p for pose in poses], axis=0)
        if global_v is None:
            _, global_p = npmotion.R.fk(self.local_R, self.root_p, self.skeleton)
            self.global_v = global_p[1:] - global_p[:-1]
            self.global_v = np.pad(self.global_v, ((1, 0), (0, 0), (0, 0)), "edge")
        else:
            self.global_v = global_v
    
    def __len__(self):
        return len(self.poses)
        
    @property
    def num_frames(self):
        return len(self.poses)
    
    @classmethod
    def from_numpy(cls, skeleton, local_R, root_p, fps=30.0):
        poses = []
        for i in range(local_R.shape[0]):
            pose = Pose.from_numpy(skeleton, local_R[i], root_p[i])
            poses.append(pose)
        return cls("default", skeleton, poses, fps=fps)

    @classmethod
    def from_torch(cls, skeleton, local_R, root_p, fps=30.0):
        poses = []
        for i in range(local_R.shape[0]):
            pose = Pose.from_numpy(skeleton, local_R[i].cpu().numpy(), root_p[i].cpu().numpy())
            poses.append(pose)
        return cls("default", skeleton, poses, fps=fps)

    def make_window(self, start, end):
        return Motion(
            self.name,
            self.skeleton,
            copy.deepcopy(self.poses[start:end]),
            copy.deepcopy(self.global_v[start:end]),
            self.fps
        )

    def update(self):
        """
        Called whenever self.local_R or self.root_p are changed.
        """
        for i in range(self.num_frames):
            self.poses[i].local_R = self.local_R[i]
            self.poses[i].root_p = self.root_p[i]
    
    def get_pose_by_frame(self, frame):
        return self.poses[frame]

    def get_pose_by_time(self, time):
        frame = int(time * self.fps)
        return self.poses[frame]

    """ Alignment functions """
    def align_to_origin_by_frame(self, frame):
        self.root_p -= self.root_p[frame] * npconst.XZ()
        self.update()
    
    def align_to_forward_by_frame(self, frame, forward=npconst.FORWARD()):
        forward_from = self.poses[frame].forward
        forward_to   = npmotion.normalize(forward * npconst.XZ())

        # if forward_from and forward_to are (nearly) parallel, do nothing
        if np.dot(forward_from, forward_to) > 0.999999:
            return
        
        axis = npmotion.normalize(np.cross(forward_from, forward_to))
        angle = np.arccos(np.dot(forward_from, forward_to))
        R_delta = npmotion.R.from_A(angle, axis)
        
        # update root rotation - R: (nof, noj, 3, 3), R_delta: (3, 3)
        self.local_R[:, 0] = np.matmul(R_delta, self.local_R[:, 0])

        # update root position - R_delta: (3, 3), p: (nof, 3) -> (nof, 3)
        self.root_p = self.root_p - self.poses[frame].base
        self.root_p = np.matmul(R_delta, self.root_p.T).T + self.poses[frame].base
        self.root_p = self.root_p

        # update velocity - R_delta: (3, 3), v: (nof, noj, 3) -> (nof, noj, 3)
        self.global_v = np.einsum("ij,klj->kli", R_delta, self.global_v)
        
        self.update()
    
    def align_by_frame(self, frame, forward=npconst.FORWARD()):
        self.align_to_origin_by_frame(frame)
        self.align_to_forward_by_frame(frame, forward)
    
    """ Manipulation functions """
    def translate_root(self, delta):
        self.root_p += delta
        self.update()

    """ Rendering functions """
    def render_by_time(self, time, albedo=glm.vec3(1, 0, 0)):
        frame = min(int(time * self.fps), self.num_frames - 1)
        self.poses[frame].draw(albedo=albedo)
    
    def render_by_frame(self, frame, albedo=glm.vec3(1, 0, 0)):
        frame = min(frame, self.num_frames - 1)
        self.poses[frame].draw(albedo=albedo)

    """ Motion features """
    def get_local_R6(self):
        return npmotion.R6.from_R(self.local_R)
    
    def get_root_v(self):
        return self.global_v[:, 0, :]

    def get_contacts(self, lfoot_idx, rfoot_idx, velfactor=2e-6, keep_shape=False):
        """
        Extracts binary tensors of feet contacts

        :param pos: tensor of global positions of shape (Timesteps, Joints, 3)
        :param lfoot_idx: indices list of left foot joints
        :param rfoot_idx: indices list of right foot joints
        :param velfactor: velocity threshold to consider a joint moving or not
        :return: binary tensors of left foot contacts and right foot contacts
        """
        contacts_l = np.sum(self.global_v[:, lfoot_idx] ** 2, axis=-1) < velfactor
        contacts_r = np.sum(self.global_v[:, rfoot_idx] ** 2, axis=-1) < velfactor
        res = np.concatenate([contacts_l, contacts_r], axis=-1, dtype=np.float32)
        return res