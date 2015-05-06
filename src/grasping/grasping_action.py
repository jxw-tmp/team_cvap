#!/usr/bin/env python
import moveit_commander

import rospy

import actionlib

import amazon_challenge_bt_actions.msg

from std_msgs.msg import String
import sys

import tf
import PyKDL as kdl
import pr2_moveit_utils.pr2_moveit_utils as pr2_moveit_utils
from pr2_controllers_msgs.msg import Pr2GripperCommand
from geometry_msgs.msg import Pose, PoseStamped
from tf_conversions import posemath
import math
from calibrateBase import baseMove
from amazon_challenge_motion.bt_motion import BTMotion
import amazon_challenge_bt_actions.msg


class BTAction(object):
    # create messages that are used to publish feedback/result
    _feedback = amazon_challenge_bt_actions.msg.BTFeedback()
    _result = amazon_challenge_bt_actions.msg.BTResult()

    def __init__(self, name):
        self._action_name = name
        self._as = actionlib.SimpleActionServer(self._action_name, amazon_challenge_bt_actions.msg.BTAction,
                                                execute_cb=self.execute_cb, auto_start=False)
        self._as.start()
        self.pub_grasped = rospy.Publisher('object_grasped', String)
        self.pub_pose = rospy.Publisher('hand_pose', PoseStamped)
        self.pub_rate = rospy.Rate(30)
        while not rospy.is_shutdown():
            try:
                self.left_arm = moveit_commander.MoveGroupCommander('left_arm')
                self.left_arm.set_planning_time(4.0)
                self.right_arm = moveit_commander.MoveGroupCommander('right_arm')
                self.right_arm.set_planning_time(4.0)
                break
            except:
                pass

        self.listener = tf.TransformListener()
        moveit_commander.roscpp_initialize(sys.argv)
        rospy.Subscriber("/amazon_next_task", String, self.get_task)
        self._item = ""
        self._bin = ""
        self.l_gripper_pub = rospy.Publisher('/l_gripper_controller/command', Pr2GripperCommand)
        self.r_gripper_pub = rospy.Publisher('/r_gripper_controller/command', Pr2GripperCommand)
        self.pre_distance = -0.16
        self.ft_switch = True
        self.lifting_height = 0.04
        self.retreat_distance = 0.35
        self.graspingStrategy = 0 # 0 for sideGrasping and 1 for topGrasping
        self.topGraspHeight = 0.1
        self.topGraspingFrame = 'base_link'
        self.sideGraspingTrials = 10
        self.sideGraspingTolerance = math.radians(45)

        # base movement
        self._bm = baseMove.baseMove(verbose=False)
        self._bm.setPosTolerance(0.02)
        self._bm.setAngTolerance(0.006)
        self._bm.setLinearGain(0.4)
        self._bm.setAngularGain(1)

        self._tool_size = rospy.get_param('/tool_size', [0.16, 0.02, 0.04])
        rospy.loginfo('Grapsing action ready')

    def flush(self):
        self._item = ""
        self._bin = ""

    def transformPoseToRobotFrame(self, planPose, planner_frame):

        pre_pose_stamped = PoseStamped()
        pre_pose_stamped.pose = posemath.toMsg(planPose)
        pre_pose_stamped.header.stamp = rospy.Time()
        pre_pose_stamped.header.frame_id = planner_frame

        while not rospy.is_shutdown():
            try:
                robotPose = self.listener.transformPose('/base_link', pre_pose_stamped)
                break
            except:
                pass

        self.pub_pose.publish(robotPose)
        return robotPose


    def RPYFromQuaternion(self, q):
        return tf.transformations.euler_from_quaternion([q[0], q[1], q[2], q[3]])




    def execute_cb(self, goal):
        # publish info to the console for the user
        rospy.loginfo('Starting Grasping')

        # start executing the action
        # check that preempt has not been requested by the client
        if self._as.is_preempt_requested():
            rospy.loginfo('Action Halted')
            self._as.set_preempted()
            return

        rospy.loginfo('Executing Grasping')

        if self.graspingStrategy[0] == 0:
            status = self.sideGrasping()
        elif self.graspingStrategy[0] == 1:
            status = self.topGrasping()
        else:
            self.flush()
            rospy.logerr('No strategy found to grasp')
            self.set_status('FAILURE')

    def topGrasping(self):

        while not rospy.is_shutdown():
            try:
                tp = self.listener.lookupTransform('/base_link', "/" + self._item + "_detector", rospy.Time(0))
                rospy.loginfo('got new object pose')
                tpRPY = self.RPYFromQuaternion(tp[1])
                break
            except:
                pass

        self.open_left_gripper()


        tool_frame_rotation = kdl.Rotation.RPY(math.radians(180), math.radians(30), 0)
        '''
        PRE-GRASPING
        '''

        pre_pose = kdl.Frame(tool_frame_rotation, kdl.Vector( tp[0][0] + self.pre_distance, tp[0][1], tp[0][2] + self.topGraspHeight))


        try:
            pr2_moveit_utils.go_tool_frame(self.left_arm, pre_pose, base_frame_id = self.topGraspingFrame, ft=self.ft_switch,
                                           wait=True, tool_x_offset=self._tool_size[0])
        except:
            self.flush()
            rospy.logerr('exception in PRE-GRASPING')
            self.set_status('FAILURE')
            return


        '''
        REACHING
        '''

        reaching_pose = kdl.Frame(tool_frame_rotation, kdl.Vector( tp[0][0], tp[0][1], tp[0][2] + self.topGraspHeight))

       
        try:
            pr2_moveit_utils.go_tool_frame(self.left_arm, reaching_pose, base_frame_id = self.topGraspingFrame, ft=self.ft_switch,
                                           wait=True, tool_x_offset=self._tool_size[0])
        except:
            self.flush()
            rospy.logerr('exception in REACHING')
            self.set_status('FAILURE')
            return

        '''
        TOUCHING
        '''

        touching_pose = kdl.Frame(tool_frame_rotation, kdl.Vector( tp[0][0], tp[0][1], tp[0][2] + 0.06))

        
        try:
            pr2_moveit_utils.go_tool_frame(self.left_arm, touching_pose, base_frame_id = self.topGraspingFrame, ft=self.ft_switch,
                                           wait=True, tool_x_offset=self._tool_size[0])
        except:
            self.flush()
            rospy.logerr('exception in REACHING')
            self.set_status('FAILURE')
            return

        '''
        GRASPING
        '''

        self.close_left_gripper()

        '''
        LIFTING
        '''

        lifting_pose = kdl.Frame(tool_frame_rotation, kdl.Vector( tp[0][0], tp[0][1], tp[0][2] + self.topGraspHeight))
        


        try:
            pr2_moveit_utils.go_tool_frame(self.left_arm, lifting_pose, base_frame_id = self.topGraspingFrame, ft=self.ft_switch,
                                           wait=True, tool_x_offset=self._tool_size[0])
        except:
            self.flush()
            rospy.logerr('exception in PRE-GRASPING')
            self.set_status('FAILURE')
            return

        '''
        RETREATING
        '''
        retreating_pose = kdl.Frame(tool_frame_rotation, kdl.Vector( tp[0][0] + self.pre_distance, tp[0][1], tp[0][2] + self.topGraspHeight))



        try:
            pr2_moveit_utils.go_tool_frame(self.left_arm, retreating_pose, base_frame_id = self.topGraspingFrame, ft=self.ft_switch,
                                           wait=True, tool_x_offset=self._tool_size[0])
        except:
            self.flush()
            rospy.logerr('exception in PRE-GRASPING')
            self.set_status('FAILURE')
            return



        self.pub_grasped.publish("SUCCESS")
        self.set_status('SUCCESS')
        self.pub_rate.sleep()
        return

    def sideGrasping(self):

        while not rospy.is_shutdown():
            try:
                tp = self.listener.lookupTransform('/base_link', "/" + self._item + "_detector", rospy.Time(0))
                binFrame = self.listener.lookupTransform("/" + "shelf_" + self._bin, "/" + self._item + "_detector", rospy.Time(0))
                liftShift = 0.15 - binFrame[0][1]
                rospy.logerr('liftShift')
                rospy.logerr(liftShift)
                rospy.loginfo('got new object pose')
                tpRPY = self.RPYFromQuaternion(tp[1])
                objBinRPY = self.RPYFromQuaternion(binFrame[1])
                break
            except:
                pass


        if abs(objBinRPY[1]) > 0.5:
            rospy.logerr('require pushing the object')
            self.set_status('FAILURE')
            return

        angle_step = 0

        if objBinRPY[2] < 0:
            angle_step = -self.sideGraspingTolerance / (self.sideGraspingTrials - 1.0)
        else:
            angle_step = self.sideGraspingTolerance / (self.sideGraspingTrials - 1.0)


        for i in range(self.sideGraspingTrials):

            yaw_now = angle_step * i
            
            '''
            PRE-GRASPING
            '''
            rospy.loginfo('PRE-GRASPING')
            planner_frame = '/' + self._item + "_detector"

            self.open_left_gripper()

            rospy.logerr(yaw_now)
            pre_pose = kdl.Frame(kdl.Rotation.RPY(0, 0, yaw_now), kdl.Vector( self.pre_distance, 0, 0))
            pre_pose_robot = self.transformPoseToRobotFrame(pre_pose, planner_frame)

            
            try:
                pr2_moveit_utils.go_tool_frame(self.left_arm, pre_pose_robot.pose, base_frame_id = pre_pose_robot.header.frame_id, ft=self.ft_switch,
                                               wait=True, tool_x_offset=self._tool_size[0])
            except Exception, e:
                rospy.logerr('exception in PRE-GRASPING')
                rospy.logerr(e)
                continue

            '''
            REACHING
            '''
            rospy.loginfo('REACHING')
            reaching_pose = kdl.Frame(kdl.Rotation.RPY(0, 0, yaw_now), kdl.Vector( 0.02,0,0))
            reaching_pose_robot = self.transformPoseToRobotFrame(reaching_pose, planner_frame)

        
            try:
                pr2_moveit_utils.go_tool_frame(self.left_arm, reaching_pose_robot.pose, base_frame_id = reaching_pose_robot.header.frame_id, ft=self.ft_switch,
                                               wait=True, tool_x_offset=self._tool_size[0])
            except:
                self.flush()
                rospy.logerr('exception in REACHING')
                continue

            '''
            GRASPING
            '''
            rospy.loginfo('GRASPING')
            self.close_left_gripper()
            '''
            LIFTING
            '''

            rospy.loginfo('LIFTING')

            lifting_pose = kdl.Frame(kdl.Rotation.RPY(tpRPY[0], tpRPY[1], 0), kdl.Vector( tp[0][0], tp[0][1] + liftShift, tp[0][2] + self.lifting_height))

        
            try:
                pr2_moveit_utils.go_tool_frame(self.left_arm, lifting_pose, base_frame_id = 'base_link', ft=self.ft_switch,
                                               wait=True, tool_x_offset=self._tool_size[0])
            except:
                self.flush()
                if arm_now == 'right_arm':
                    self.open_right_gripper()
                else:
                    self.open_left_gripper()
                rospy.logerr('exception in LIFTING')
                continue

            '''
            RETREATING
            '''
            rospy.loginfo('RETREATING')
            # retreating_pose = kdl.Frame(kdl.Rotation.RPY(tpRPY[0], tpRPY[1], tpRPY[2]), kdl.Vector( tp[0][0] - self.retreat_distance, tp[0][1], tp[0][2]))

        
            # try:
            #     pr2_moveit_utils.go_tool_frame(self.left_arm, retreating_pose, base_frame_id = 'base_link', ft=self.ft_switch,
            #                                    wait=True, tool_x_offset=self._tool_size[0])
            # except:
            #     self.flush()
            #     rospy.logerr('exception in RETREATING')
            #     self.set_status('FAILURE')
            #     return

            try:
                base_pos_dict = rospy.get_param('/base_pos_dict')
                column = self.get_column()
                base_pos_goal = base_pos_dict[column]
                base_pos_goal[0] -= 0.5
                self.go_base_pos_async(base_pos_goal)
            except Exception, e:
                rospy.logerr(e)
                self.flush()

                self.open_left_gripper()

                rospy.logerr('exception in RETREATING')
                self.set_status('FAILURE')
                continue

            rospy.loginfo('Grasping successfully done')
            self.flush()
            self.set_status('SUCCESS')
            return



        #IF THE ACTION HAS FAILED
        self.flush()



        self.pub_grasped.publish("FAILURE")
        self.set_status('FAILURE')
        self.pub_rate.sleep()
        return


    def set_status(self, status):
        if status == 'SUCCESS':
            self._feedback.status = 1
            self._result.status = self._feedback.status
            rospy.loginfo('Action %s: Succeeded' % self._action_name)
            self._as.set_succeeded(self._result)
        elif status == 'FAILURE':
            self._feedback.status = 2
            self._result.status = self._feedback.status
            rospy.loginfo('Action %s: Failed' % self._action_name)
            self._as.set_succeeded(self._result)
        else:
            rospy.logerr('Action %s: has a wrong return status' % self._action_name)

    def get_task(self, msg):
        text = msg.data
        text = text.replace('[','')
        text = text.replace(']','')
        words = text.split(',')
        self._bin = words[0]
        self._item = words[1]


    def go_left_gripper(self, position, max_effort):
        """Move left gripper to position with max_effort
        """
        ope = Pr2GripperCommand()
        ope.position = position
        ope.max_effort = max_effort
        self.l_gripper_pub.publish(ope)

    def go_right_gripper(self, position, max_effort):
        """Move right gripper to position with max_effort
        """
        ope = Pr2GripperCommand()
        ope.position = position
        ope.max_effort = max_effort
        self.r_gripper_pub.publish(ope)

    def close_left_gripper(self):
        self.go_left_gripper(0, 40)
        rospy.sleep(4)

    def close_right_gripper(self):
        self.go_right_gripper(0, 40)
        rospy.sleep(4)

    def open_left_gripper(self):
        self.go_left_gripper(10, 40)
        rospy.sleep(2)

    def open_right_gripper(self):
        self.go_right_gripper(10, 40)
        rospy.sleep(2)

    def go_base_pos_async(self, base_pos_goal):

        angle = base_pos_goal[5]
        pos = base_pos_goal[0:2]
        r = rospy.Rate(100.0)

        # check for preemption while the base hasn't reach goal configuration
        while not self._bm.goAngle(angle) and not rospy.is_shutdown():

            # check that preempt has not been requested by the client
            if self._as.is_preempt_requested():
                #HERE THE CODE TO EXECUTE WHEN THE  BEHAVIOR TREE DOES HALT THE ACTION
                group.stop()
                rospy.loginfo('[pregrasp_server]: action halted while moving base')
                self._as.set_preempted()
                self._success = False
                return False

            #HERE THE CODE TO EXECUTE AS LONG AS THE BEHAVIOR TREE DOES NOT HALT THE ACTION
            r.sleep()

        while not self._bm.goPosition(pos) and not rospy.is_shutdown():

            # check that preempt has not been requested by the client
            if self._as.is_preempt_requested():
                #HERE THE CODE TO EXECUTE WHEN THE  BEHAVIOR TREE DOES HALT THE ACTION
                group.stop()
                rospy.loginfo('[pregrasp_server]: action halted while moving base')
                self._as.set_preempted()
                self._success = False
                return False

            #HERE THE CODE TO EXECUTE AS LONG AS THE BEHAVIOR TREE DOES NOT HALT THE ACTION
            r.sleep()

        while not self._bm.goAngle(angle) and not rospy.is_shutdown():

            # check that preempt has not been requested by the client
            if self._as.is_preempt_requested():
                #HERE THE CODE TO EXECUTE WHEN THE  BEHAVIOR TREE DOES HALT THE ACTION
                group.stop()
                rospy.loginfo('[pregrasp_server]: action halted while moving base')
                self._as.set_preempted()
                self._success = False
                return False

            #HERE THE CODE TO EXECUTE AS LONG AS THE BEHAVIOR TREE DOES NOT HALT THE ACTION
            r.sleep()

        return True

    def get_column(self):
        '''
        For setting the base pose
        '''
        while not rospy.is_shutdown():
            try:
                if self._bin=='bin_A' or self._bin=='bin_D' or self._bin=='bin_G' or self._bin=='bin_J':
                    return 'column_1'

                elif self._bin=='bin_B' or self._bin=='bin_E' or self._bin=='bin_H' or self._bin=='bin_K':
                    return 'column_2'

                elif self._bin=='bin_C' or self._bin=='bin_F' or self._bin=='bin_I' or self._bin=='bin_L':
                    return 'column_3'

            except:
                pass




if __name__ == '__main__':
    rospy.init_node('grasp_object')
    BTAction(rospy.get_name())
    rospy.spin()
