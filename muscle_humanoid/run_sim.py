import mujoco as mj
from mujoco.glfw import glfw
import numpy as np
import os
from typing import Callable, Optional, Union, List
import scipy.linalg
import mediapy as media
import matplotlib as mpl
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import threading
import time
from queue import Queue
import xml.etree.ElementTree as ET
import imageio
import yaml
from plotting_utilities import plot_3d_pose_trajectory, \
                               plot_columns, \
                               plot_four_columns, \
                               plot_two_columns

from xml_utilities import calculate_kp_and_geom, set_geometry_params

plt.rcParams['text.usetex'] = True
# plt.rcParams.update({
#     'text.usetex': True,
#     'font.family': 'sans-serif',  # You can choose 'serif', 'sans-serif', or other font families
#     'font.sans-serif': 'Helvetica',  # Specify the font you want to use
#     'axes.labelsize': 12,
#     'font.size': 12,
#     'legend.fontsize': 10,
#     'xtick.labelsize': 10,
#     'ytick.labelsize': 10,
#     })
mpl.rcParams.update(mpl.rcParamsDefault)
# this function is a thread that uses imulse_thread_exit_flag (global variable)
# not sure if there is a way to move this function to a utility script without 
# preventing function from accessing impulse_thread_exit_flag
# precallocating arrays and queues for data sharing and data collection
# impulse_thread_exit_flag = False
perturbation_queue = Queue()
control_log_queue = Queue()
counter_queue = Queue()
perturbation_datalogger_queue = Queue()

class muscleSim:
    def __init__(self, plot_flag):
        print('simulation class object created')    
        self.plot_flag = plot_flag
        self.impulse_thread_exit_flag = True
        self.ankle_position_setpoint = 0 # [degrees]
        self.mp4_flag = False

    # this function has to stay in this script per MuJoCo documentation
    def controller(self, model, data):
        """
        Controller function for the leg. The only two parameters 
        for this function can be mujoco.model and mujoco.data
        otherwise it doesn't seem to run properly
        """
        bb_ctrl = 1
        error = self.ankle_position_setpoint - data.sensordata[0]
        # print(f'error {round(error,5)}')
        # tendon bang-bang controller
        if error > 0:
            data.ctrl[0] = bb_ctrl
            data.ctrl[1] = -bb_ctrl
        else:
            data.ctrl[0] = -bb_ctrl
            data.ctrl[1] = bb_ctrl
        # Log the actuator torque and timestep to an array for later use
        # control_torque_time_array = np.array([data.time, human_torque])
        # control_log_queue.put(control_torque_time_array)
        
        # Apply joint perturbations in Joint Space
        # data.qfrc_applied = [ q_1, q_2, q_3, q_4, q_5, q_6, q_7, q_8]
        # data.qfrc_applied[3] = rx_perturbation
        # data.qfrc_applied[4] = ry_perturbation

    def generate_large_impulse(self, perturbation_queue, impulse_time, perturbation_magnitude, impulse_period):
            

            print(f'exit flag: {self.impulse_thread_exit_flag}')
            while not self.impulse_thread_exit_flag: # run continuously once we start a thread 
                        # for this function
                
                # empty the queue from the previous impulse generation
                while not perturbation_queue.empty():
                    perturbation_queue.get()

                wait_time = np.random.uniform(impulse_period,impulse_period+1)
                time.sleep(wait_time)
                
                # Generate a large impulse
                perturbation = np.random.uniform(perturbation_magnitude, perturbation_magnitude+10)

                # this will generate either -1 or 1
                direction_bool = np.random.choice([-1,1])
                # print(direction_bool)
                # start_time = time.time()
                # delta = time.time() - start_time
                end_time = time.time() + impulse_time
                print(time.time())
                print(end_time)
                i = 0
                while time.time() < end_time:
                    # delta = time.time() - start_time
                    # time.sleep(0.000001)
                    if i % 1000 == 0:
                        print(f"impulse duration: {end_time-time.time()}")
                    i+=1
                    # Put the generated impulse into the result queue
                    perturbation_queue.put(direction_bool*perturbation)

    def load_params_from_yaml(self, file_path):
        with open(file_path, 'r') as file:
            params = yaml.safe_load(file)
        return params

    def run(self):
        
        control_log_array = np.empty((1,2))
        goal_position_data = np.empty((1,2))
        joint_position_data = np.empty((1,2))
        joint_velocity_data = np.empty((1,2))
        body_com_data = np.empty((1,4))
        body_orientation_data = np.empty((1,9))
        perturbation_data_array = np.empty((1,2))
        com_ext_force_data = np.empty((1,7))
        constraint_frc_data = np.empty((1,5))
        contact_force_sensor = np.empty((1,3))

        params = self.load_params_from_yaml('config.yaml')

        xml_path = params['config']['xml_path']
        self.plot_flag = params['config']['plotter_flag']
        self.mp4_flag = params['config']['mp4_flag']
        simend = params['config']['simend']   # duration of simulation
        
        self.ankle_position_setpoint = params['config']['ankle_position_setpoint']
        ankle_joint_initial_position = params['config']['ankle_joint_initial_position'] # ankle joint initial angle

        translation_friction_constant = params['config']['translation_friction_constant']
        rolling_friction_constant = params['config']['rolling_friction_constant']

        perturbation_time = params['config']['perturbation_time']
        perturbation_magnitude = params['config']['perturbation_magnitude']   # size of impulse
        perturbation_period = params['config']['perturbation_period']  # period at which impulse occurs

        # Define parameters for creating and storing an MP4 file of the rendered simulation
        video_file = params['config']['mp4_file_name']
        video_fps = params['config']['mp4_fps']
        frames = [] # list to store frames
        desired_viewport_height = 912

        
        simend = params['config']['simend']   # duration of simulation
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # MODIFY THE XML FILE AND INSERT THE MASS AND LENGTH
        # PROPERTIES OF INTEREST 
        M_total = params['config']['M_total'] # kilograms
        H_total = params['config']['H_total'] # meters
        m_feet, m_body, l_COM, l_foot, a, K_p, h_f = calculate_kp_and_geom \
                                                (M_total, H_total)
        
        set_geometry_params(root, 
                            m_feet, 
                            m_body, 
                            l_COM, 
                            l_foot, 
                            a, 
                            H_total, 
                            h_f, 
                            translation_friction_constant, 
                            rolling_friction_constant) # call utility functoin to set parameters of xml model

        literature_model = params['config']['lit_xml_file']
        tree.write(literature_model)
        ########
        # script_directory = os.path.dirname(os.path.abspath(__file__))
        # xml_path = os.path.join(script_directory, xml_path)
        model = mj.MjModel.from_xml_path(literature_model)  # MuJoCo XML model

        data = mj.MjData(model)                # MuJoCo data
        cam = mj.MjvCamera()                        # Abstract camera
        opt = mj.MjvOption()                        # visualization options

        # Init GLFW, create window, make OpenGL context current, request v-sync
        glfw.init()
        window = glfw.create_window(1200, 900, "Demo", None, None)
        glfw.make_context_current(window)
        glfw.swap_interval(1)

        # initialize visualization data structures
        mj.mjv_defaultCamera(cam)
        mj.mjv_defaultOption(opt)
        # opt.flags[mj.mjtVisFlag.mjVIS_CONTACTPOINT] = True
        opt.flags[mj.mjtVisFlag.mjVIS_CONTACTFORCE] = params['config']['visualize_contact_force']
        opt.flags[mj.mjtVisFlag.mjVIS_PERTFORCE] = params['config']['visualize_perturbation_force']
        # opt.flags[mj.mjtVisFlag.mjVIS_JOINT] = True
        opt.flags[mj.mjtVisFlag.mjVIS_ACTUATOR] = params['config']['visualize_actuators']
        # opt.flags[mj.mjtVisFlag.mjVIS_COM] = True
        # opt.flags[mj.mjtLabel.mjLABEL_JOINT] = True
        opt.flags[mj.mjtFrame.mjFRAME_GEOM] = True
        # opt.flags[mj.mjtFrame.mjFRAME_WORLD] = True 
        # opt.flags[mj.mjtFrame.mjFRAME_CONTACT] = True
        # opt.flags[mj.mjtFrame.mjFRAME_BODY] = True
        # opt.mjVIS_COM = True
        model.opt.timestep = params['config']['simulation_timestep']

        # tweak map parameters for visualization
        model.vis.map.force = 0.1 # scaling parameter for force vector's length
        model.vis.map.torque = 0.1 # scaling parameter for control torque

        # tweak scales of contact visualization elements
        model.vis.scale.contactwidth = 0.05 # width of the floor contact point
        model.vis.scale.contactheight = 0.01 # height of the floor contact point
        model.vis.scale.forcewidth = 0.03 # width of the force vector
        model.vis.scale.com = 0.2 # com radius
        model.vis.scale.actuatorwidth = 0.1 # diameter of visualized actuator
        model.vis.scale.actuatorlength = 0.1 # thickness of visualized actuator
        model.vis.scale.jointwidth = 0.025 # diameter of joint arrows

        # tweaking colors of stuff
        model.vis.rgba.contactforce = np.array([0.7, 0., 0., 0.5], dtype=np.float32)
        model.vis.rgba.force = np.array([0., 0.7, 0., 0.5], dtype=np.float32)
        model.vis.rgba.joint = np.array([0.7, 0.7, 0.7, 0.5])
        model.vis.rgba.actuatorpositive = np.array([0., 0.9, 0., 0.5])
        model.vis.rgba.actuatornegative = np.array([0.9, 0., 0., 0.5])
        model.vis.rgba.com = np.array([1.,0.647,0.,0.5])

        scene = mj.MjvScene(model, maxgeom=10000)
        context = mj.MjrContext(model, mj.mjtFontScale.mjFONTSCALE_150.value)

        # Set camera configuration
        # Set camera configuration
        cam.azimuth = params['config']['camera_azimuth']
        cam.distance = params['config']['camera_distance']
        cam.elevation = params['config']['camera_elevation']

        lookat_string_xyz = params['config']['camera_lookat_xyz']
        print(f'lookat string: {lookat_string_xyz}')
        # print(lookat_string_xyz)
        # do some stuff to take a comma separated string and convert it to a tuple
        res = tuple(map(float, lookat_string_xyz.split(', ')))
        # print(f'res: {res}')
        # get the x, y, z camera coords from the res tuple
        cam.lookat = np.array([res[0], res[1], res[2]])

        # INITIAL CONDITIONS FOR JOINT VELOCITIES
        # data.qvel[0]= 0              # hinge joint at top of body
        # data.qvel[1]= 0              # slide / prismatic joint at top of body in x direction
        # data.qvel[2]= 0              # slide / prismatic joint at top of body in z direction
        # data.qvel[3]= 0.4           # hinge joint at ankle

        # INITIAL CONDITIONS FOR JOINT POSITION
        # data.qpos[0]= 5*np.pi/180 # hinge joint at top of body
        # data.qpos[1]=0          # slide / prismatic joint at top of body in x direction
        # data.qpos[2]=-np.pi/6   # slide / prismatic joint at top of body in z direction
        data.qpos[3]= ankle_joint_initial_position # hinge joint at ankle

        # ID number for the ankle joint, used for data collection
        ankle_joint_id = mj.mj_name2id(model, mj.mjtObj.mjOBJ_JOINT, "ankle_hinge")

        # ID number for the body geometry
        human_body_id = mj.mj_name2id(model, mj.mjtObj.mjOBJ_BODY, "shin_body")

        recorded_control_counter = 0

        control_flag = params['config']['controller_flag']
        if control_flag:
            # PASS CONTROLLER METHOD
            # TO MUJOCO THING THAT DOES CONTROL... DONT FULLY
            # UNDERSTAND WHAT IS GOING ON HERE BUT IT DOES SEEM
            # TO WORK.
            mj.set_mjcb_control(self.controller)
            pass
        # else:
        #     # use prerecorded torque values if this is the case
        #     torque_csv_file_path = os.path.join(script_directory, "recorded_torques_test.csv")
        #     recorded_torques = np.loadtxt(torque_csv_file_path, delimiter=',')
            # print(recorded_torques)

        # start the thread that generates the perturbation impulses
        self.impulse_thread_exit_flag = False
        perturbation_thread = threading.Thread \
            (target=self.generate_large_impulse, 
            daemon=True, 
            args=(perturbation_queue,perturbation_time, perturbation_magnitude, perturbation_period) )
        if params['config']['apply_perturbation']:
            perturbation_thread.start()
        start_time = time.time()

        while not glfw.window_should_close(window):
            simstart = data.time
            
            while (data.time - simstart < 1.0/60.0):
                # print(data.qpos[0])
                # print(f'time delta: {time.time() - start_time}')
                # data.ctrl[0] = 1
                # data.ctrl[1] = 1
                x_perturbation=0

                if not perturbation_queue.empty():
                    
                    x_perturbation = perturbation_queue.get()
                    perturbation_datalogger_queue.put(x_perturbation)
                
                # data.xfrc_applied[i] = [ F_x, F_y, F_z, R_x, R_y, R_z]
                data.xfrc_applied[2] = [x_perturbation, 0, 0, 0., 0., 0.]

                # if data.qpos[0] > np.pi/4 or data.qpos[0] < -np.pi/4:
                #     simend = data.time
                #print(f'sensor data: {data.sensordata[0]}')
                # if we aren't using the PD control mode and instead using prerecorded data, then
                # we should set the control input to the nth value of the recorded torque array
                # if not control_flag:
                #     data.ctrl[0] = recorded_torques[recorded_control_counter,1]
                #print(f"simulation time: {data.time}")
                # step the simulation forward in time
                mj.mj_step(model, data)
            
                # collect data from the control log if it isn't empty, this is used in the PD control mode
                
                control_log_array = np.vstack((control_log_array, np.array([data.time,data.qfrc_actuator[0]]) ))
                
                # collect data from perturbation for now perturbation is 1D so we have (n,2) array of pert. data
                if not perturbation_datalogger_queue.empty():
                    perturbation_data_array = np.vstack((perturbation_data_array, np.array([data.time, perturbation_datalogger_queue.get()]) ))
                else:
                    # if the perturbation queue is empty then the perturbation is zero
                    # print(time.time())
                    perturbation_data_array = np.vstack((perturbation_data_array, np.array([data.time, 0.]) ))
                    
                # collect joint position and velocity data from simulation for visualization
                joint_position_data = np.vstack((joint_position_data, np.array([data.time, 180/np.pi*data.qpos[ankle_joint_id]]) ))
                joint_velocity_data = np.vstack((joint_velocity_data, np.array([data.time, 180/np.pi*data.qvel[ankle_joint_id]]) ))
                goal_position_data = np.vstack((goal_position_data, np.array([0,180/np.pi*self.ankle_position_setpoint]) ))
                # collect center of mass data for ID associated with human body element of XML model
                constraint_frc_data = np.vstack((constraint_frc_data, np.array([data.time,data.qfrc_constraint[0],data.qfrc_constraint[1],data.qfrc_constraint[2],data.qfrc_constraint[3]])))
                contact_force_sensor = np.vstack((contact_force_sensor, np.array([data.time, data.sensordata[1], data.sensordata[2]]) ))

                com = data.xipos[human_body_id]
                # collect 1x9 orientation vector for ID associated with human body element of XML model
                # format is [r_11, r_12, r_13, r_21, r_22, r_23, r_31, r_32, r_33]
                orientation_vector = data.ximat[human_body_id]
                # add center of mass data to array
                body_com_data = np.vstack((body_com_data, np.array([data.time, com[0], com[1], com[2]]) ))
                # add orientation data to array
                # orientation_matrix = np.reshape(orientation_vector, (3,3))
                body_orientation_data = np.vstack((body_orientation_data, orientation_vector))
            
            if (data.time>=simend):
                self.impulse_thread_exit_flag = True
                if params['config']['apply_perturbation']:
                    perturbation_thread.join()
                break;
            
            # get framebuffer viewport
            viewport_width, viewport_height = glfw.get_framebuffer_size(
                window)
            viewport = mj.MjrRect(0, 0, viewport_width, viewport_height)

            # Update scene and render
            mj.mjv_updateScene(model, data, opt, None, cam,
                            mj.mjtCatBit.mjCAT_ALL.value, scene)
            mj.mjr_render(viewport, scene, context)


            ###### CODE TO CAPTURE FRAMES FOR MP4 VIDEO GENERATION ##########
            # Capture the frame
            rgb_array = np.empty((viewport_height, viewport_width, 3), dtype=np.uint8)
            depth_array = np.empty((viewport_height, viewport_width), dtype=np.float32)
            mj.mjr_readPixels(rgb=rgb_array, depth=depth_array, viewport=viewport, con=context)
            rgb_array = np.flipud(rgb_array)
            # Append the frame to the list
            frames.append(rgb_array) 
            #################################################################

            # swap OpenGL buffers (blocking call due to v-sync)
            glfw.swap_buffers(window)

            # process pending GUI events, call GLFW callbacks
            glfw.poll_events()

        # this writes the list of frames we collected to an mp4 file and makes it a video
        if self.mp4_flag:
            imageio.mimwrite(video_file, frames, fps=video_fps)
        
        print('terminated')
        glfw.terminate()
    
        np.savetxt('joint_position_data', joint_position_data, delimiter=",", fmt='%.3f')
        np.savetxt('joint_velocity_data', joint_velocity_data, delimiter=",", fmt='%.3f')
        np.savetxt('constraint_frc_data', constraint_frc_data, delimiter=',', fmt='%.3f')
        np.savetxt('perturbation_data', perturbation_data_array, delimiter=",",fmt="%.3f")
        np.savetxt('contact_force_sensor', contact_force_sensor, delimiter=',', fmt="%.3f")

        ##### PLOTTING CODE ######################
        ##########################################
        if self.plot_flag:
            plot_3d_pose_trajectory(body_com_data, body_orientation_data)
            # if control_flag:
            #     torque_csv_file_path = os.path.join(script_directory, "recorded_torques_test.csv")
            #     np.savetxt(torque_csv_file_path, control_log_array[1:,:], delimiter=",")
            # plot_columns(control_log_array, '$\\bf{Control\;Torque, \\it{\\tau_{ankle}}}$')
            # else:
            #     plot_columns(recorded_torques, 'Control Torque')
            plot_columns(perturbation_data_array, "perturbation versus time")
            plot_two_columns(joint_position_data, goal_position_data, "Actual Position", "Goal Position")
            plot_columns(joint_velocity_data, "Joint Velocity")
            plot_four_columns(joint_position_data, 
                            goal_position_data, 
                            joint_velocity_data, 
                            control_log_array,
                            "joint actual pos.",
                            "joint goal pos.",
                            "joint vel.",
                            "front muscle control input")

        ###########################################
        ###########################################

if __name__ == "__main__":

    sim_class = muscleSim(True)
    sim_class.run()
