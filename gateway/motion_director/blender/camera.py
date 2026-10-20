"""Deterministic follow-rig solver, independent of Blender and model prompts."""
import math


def spring(error, velocity, damping, dt):
    omega=2/max(.001,damping)
    decay=math.exp(-omega*dt)
    term=velocity+omega*error
    return (error+term*dt)*decay, (velocity-omega*term*dt)*decay


class Anchor:
    def __init__(self):
        self.position=self.goal=self.velocity=None

    def step(self,goal,velocity,dt,damping):
        goal=tuple(goal);velocity=tuple(velocity)
        if self.position is None or damping==0:
            self.position=self.goal=goal;self.velocity=velocity
            return goal
        pairs=[spring(self.position[i]-self.goal[i],self.velocity[i]-velocity[i],damping,dt) for i in range(3)]
        self.position=tuple(goal[i]+pairs[i][0] for i in range(3))
        self.velocity=tuple(velocity[i]+pairs[i][1] for i in range(3));self.goal=goal
        return self.position


class FollowRig:
    def __init__(self,spec):
        self.spec=spec;self.anchor=Anchor();self.focus=Anchor()
        self.heading=None;self.angular_velocity=0

    def step(self,target,velocity,yaw,dt,explicit=None,orbit_angle=None):
        c=self.spec;damping=c.get('damping',.2)
        anchor=self.anchor.step(target,velocity,dt,damping)
        aim=self.focus.step((target[0],target[1],target[2]+c['aim_height']),velocity,dt,damping*.5)
        if self.heading is None or damping==0:
            self.heading=yaw;self.angular_velocity=0
        else:
            error=(self.heading-yaw+math.pi)%(2*math.pi)-math.pi
            error,self.angular_velocity=spring(error,self.angular_velocity,damping,dt)
            self.heading=yaw+error
        if explicit is not None:
            position=tuple(explicit)  # authored world path retains its timing
        elif orbit_angle is not None:
            position=(anchor[0]+math.sin(orbit_angle)*c['distance'],anchor[1]+math.cos(orbit_angle)*c['distance'],anchor[2]+c['height'])
        else:
            position=(anchor[0]-math.sin(self.heading)*c['distance'],anchor[1]+math.cos(self.heading)*c['distance'],anchor[2]+c['height'])
        return position,aim
