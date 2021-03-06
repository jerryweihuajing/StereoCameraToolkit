"""The class of binocular camera """
# function comment template
"""name
    description
Args:
            
        
Returns:

"""
# class comment template
"""The class of 

    description

Attributes:      

"""

import cv2
import numpy as np
from tqdm import tqdm
import sys
import yaml
import logging
import glob
import time

from ModelSet.settings import *
from ModelUtil.util import *
from ModelCamera import Camera
from ModelLoader import Loader
from ModelEvaluator import Evaluator

class StereoCamera(object):
    """class of binocular camera

        Mainly used to calculate fundamental matrix etc.

    Attributes:
        camera_left: class camera 
        camera_right: class camera

        FM: Fundamental matrix [3x3]
        FE: Estimated Fundamental matrix [3x3]
        EM: Essencial matrix [3x3]
        match_pts: matching points
        R_relate: 
        t_relate: 

        Config: a dictionary can be used to set parameters   

            
    """
    def __init__(self, name):
        """
        """
        self.name = name
        self.camera_left = Camera(self.name+'_left')
        self.camera_right = Camera(self.name+'_right')

        self.FM = np.zeros((3,3))

        self.FE = np.zeros((3,3))
        self.match_pts1 = None
        self.match_pts2 = None
        self.epi_cons = 0.0
        self.sym_epi_dis = 0.0
        self.epi_angle = 0.0
        self.obj_pts = None
        self.img_pts_l = None
        self.img_pts_r = None
        self.stereo_pts_flag = False

        self.F_calib = np.zeros((3,3))


        self.EE = np.zeros((3,3))
        self.E_calib = np.zeros((3,3))

        self.R_relate = np.zeros((3,3))
        self.t_relate = np.zeros((3,1))

        self.loader = Loader()

        self.stereo_calib_err = 0.
        self.stereo_calib_flag = False

        self.Evaluator = Evaluator()
    
    def init_camera_by_config(self, config_left=None, config_right=None):
        """
        """
        self.camera_left.init_by_config(config_left)
        self.camera_right.init_by_config(config_right)

    def init_stereo_by_config(self, config_path):
        """name
            description
        Args:
                    
                
        Returns:

        """
        with open(config_path) as f:
            logging.info('Initial stereo parameters from '+config_path)
            config = yaml.load(f)
            self.FE = np.array(config['Estimated_F'])
            self.E_calib = np.array(config['Calibrated_E'])
            self.F_calib = np.array(config['Calibrated_F'])
            self.EE = np.array(config['Estimated_E'])
            self.FM = np.array(config['F_GT'])
            self.R_relate = np.array(config['R'])
            self.t_relate = np.array(config['t']) 
            self.stereo_calib_err = config['stereo_calib_err']
            self.stereo_calib_flag = config['stereo_calib_flag']
            
            logging.info('Stereo parameters load done.')

    def load_FM(self, load_FM_mod = 'txt', F_flie='', index = 0):
        """name
            description
        Args:
            F_flie: File path which stores Fundamental matrix
            Index: the i-th F

        Returns:

        """

        if load_FM_mod == 'txt':
            self.FM = self.loader.Load_F_txt(F_flie)
        if load_FM_mod == 'f_list':
            self.FM = self.loader.load_F_form_Fs(F_flie, index)
        if load_FM_mod == 'f_index_list':
            self.FM = self.loader.Load_F_index(F_flie, index)
        if load_FM_mod == 'KITTI':
            self.FM = self.loader.LoadFMGT_KITTI(F_flie)
        
    def __get_normalized_F(self, F, mean, std, size=None):
        """Normalize Fundamental matrix

        """
        if size is None:
            A_resize = np.eye(3)
        else:
            orig_w, orig_h = self.shape
            new_w, new_h = size
            A_resize = np.array([
                [new_w/float(orig_w), 0.,  0.],
                [0., new_h/float(orig_h), 0.],
                [0., 0., 1.]
            ])
        A_center = np.array([
            [1, 0, -mean[0]],
            [0, 1, -mean[1]],
            [0, 0, 1.]
        ])
        A_normvar = np.array([
            [np.sqrt(2.)/std[0], 0, 0],
            [0, np.sqrt(2.)/std[1], 0],
            [0, 0, 1.]
        ])
        A = A_normvar.dot(A_center).dot(A_resize)
        A_inv = np.linalg.inv(A) 
        F = A_inv.T.dot(F).dot(A_inv)
        F /= F[2,2]
        return F

    def __get_max_norm_F(self, F):
        """name
            description
        Args:
                    
                
        Returns:

        """
        F_abs = abs(F)
        F_res = F / F_abs.max()
        return F_res

    def __sift_and_find_match(self, img1, img2):
        """
        Args:
            img1
            img2
        Returns:
            pts1
            pts2
        """
        sift = cv2.xfeatures2d.SIFT_create()

        # find the keypoints and descriptors with SIFT
        kp1, des1 = sift.detectAndCompute(img1,None)
        kp2, des2 = sift.detectAndCompute(img2,None)

        # FLANN parameters
        FLANN_INDEX_KDTREE = 0
        index_params = dict(algorithm = FLANN_INDEX_KDTREE, trees = 5)
        search_params = dict(checks=50)

        flann = cv2.FlannBasedMatcher(index_params,search_params)
        matches = flann.knnMatch(des1,des2,k=2)

        good = []
        pts1 = []
        pts2 = []

        # ratio test as per Lowe's paper
        for i,(m,n) in enumerate(matches):
            if m.distance < 0.8*n.distance:
                good.append(m)
                pts2.append(kp2[m.trainIdx].pt)
                pts1.append(kp1[m.queryIdx].pt)
        pts1 = np.int32(pts1)
        pts2 = np.int32(pts2)
        F,mask = cv2.findFundamentalMat(pts1,pts2,cv2.FM_LMEDS)

        # mask 是之前的对应点中的内点的标注，为1则是内点。
        # select the inlier points
        # pts1 = pts1[mask.ravel() == 1]
        # pts2 = pts2[mask.ravel() == 1]
        self.match_pts1 = np.int32(pts1)
        self.match_pts2 = np.int32(pts2)
        return self.match_pts1, self.match_pts2

    def __matching_points_filter(self, point_len = -1):
        """name
            description
        Args:
            point_len - the pre-set matches length
        Returns:
            flag - True for the filtered matches is enough as the points_len set
        """
        try:
            self.FM.all()
        except AttributeError:
            sys.exit("Warning: Finding good matches without F_gt.")
            
        print("Use F_GT to screening matching points")
        print('Before screening, points length is {:d}'.format(len(match_pts1)))
        leftpoints = []
        rightpoints = []
        sheld = 0.1
        epsilon = 1e-5
        F = self.FM
        # use sym_epi_dist to screen
        for p1, p2 in zip(self.match_pts1, self.match_pts2):
            hp1, hp2 = np.ones((3,1)), np.ones((3,1))
            hp1[:2,0], hp2[:2,0] = p1, p2
            fp, fq = np.dot(F, hp1), np.dot(F.T, hp2)
            sym_jjt = 1./(fp[0]**2 + fp[1]**2 + epsilon) + 1./(fq[0]**2 + fq[1]**2 + epsilon)
            err = ((np.dot(hp2.T, np.dot(F, hp1))**2) * (sym_jjt + epsilon))
            # print(err)
            
            if err < sheld:
                leftpoints.append(p1)
                rightpoints.append(p2)            

            self.match_pts1 = np.array(leftpoints)
            self.match_pts2 = np.array(rightpoints)
            print('After screening, points length is {:d}'.format(len(self.match_pts1)))

        # control the length of matching points
        if point_len != -1 and point_len <= len(self.match_pts1):
            self.match_pts1 = self.match_pts1[:point_lens]
            self.match_pts2 = self.match_pts2[:point_lens]
            print("len=",self.match_pts1.shape)
        
        if self.match_pts1.shape[0] < point_len:
            return False

        return True

    def cameras_load_imgs(self, load_path, load_mod = 'norm', load_num = -1):
        """name
            description
        Args:
            load_path:
                load_path/left/*.jpg
                load_path/right/*.jpg
                
        Returns:

        """
        left_path = os.path.join(load_path, 'left')
        right_path = os.path.join(load_path, 'right')
        logging.info('load images from %s /left & /right'% load_path)
        logging.info('Stereo images loading...')
        if load_mod == 'norm':  
            self.camera_left.load_images(left_path, 'imgs', load_num)
            self.camera_right.load_images(right_path, 'imgs', load_num)
        elif load_mod == 'gray':
            self.camera_left.load_images(left_path,'Calibration', load_num)
            self.camera_right.load_images(left_path,'Calibration', load_num)

    def ExactGoodMatch(self,filter = False,point_len = -1, index = 0):
        """Get matching points & Use F_GT to get good matching points
            1.use SIFT to exact feature points 
            if filter
            2.calculate metrics use F_GT and screening good matches
            ! use it only you have the F_GT
            :output
                bool - filter success or failed cause the matches are not enough
        """
        if self.camera_left.Image_num > 1:
            img1 = self.camera_left.Image[index].astype(np.uint8)   # left image
            img2 = self.camera_right.Image[index].astype(np.uint8)  # right image
        else:
            img1 = self.camera_left.Image.astype(np.uint8)   
            img2 = self.camera_right.Image.astype(np.uint8)

        self.__sift_and_find_match(img1, img2)

        # use F_GT to select good match
        if filter:
            flag = self.__matching_points_filter(point_len=point_len)
            return flag
        
        return True

    def EstimateFM(self,method="RANSAC", index = 0):
        """Estimate the fundamental matrix 
            :para 
                method: which method you use
                    1.RANSAC
                    2.LMedS
                    3.DL(Deep Learning)
                    4.8Points
                index:
                    which image you use
            :output 
                change self.FE
                return time cost 
        """
        time_start = 0
        time_end = 0

        try: 
            self.match_pts1.all()
        except AttributeError:
            print('Exact matching points')
            self.ExactGoodMatch(index=index)

        if method == "RANSAC":
            limit_length = len(self.match_pts1)
            print('Use RANSAC with %d points' %limit_length)
            time_start = time.time()
            FE, self.Fmask = cv2.findFundamentalMat(self.match_pts1[:limit_length],
                                                    self.match_pts2[:limit_length],
                                                    cv2.FM_RANSAC)
            time_end = time.time()

        elif method == "LMedS":
            limit_length = len(self.match_pts1)
            print('Use LMEDS with %d points' %len(self.match_pts1))
            time_start = time.time()
            FE, self.Fmask = cv2.findFundamentalMat(self.match_pts1[:limit_length],
                                                    self.match_pts2[:limit_length],
                                                    cv2.FM_LMEDS)
            time_end = time.time()
            
        elif method == "8Points":
            print('Use 8 Points algorithm')
            i = -1
            while True:
                # i = np.random.randint(0,len(self.match_pts1)-7)
                i += 1
                time_start = time.time()
                FE, self.Fmask = cv2.findFundamentalMat(self.match_pts1[i:i+8],
                                                    self.match_pts2[i:i+8],
                                                    cv2.FM_8POINT, 0.1, 0.99)
                time_end = time.time()
                
                print('Points index: ',i)
                try: 
                    FE.all()
                    break
                except AttributeError:
                    continue

            
        elif method == "DL":
            # get the mask
            FE, self.Fmask = cv2.findFundamentalMat(self.match_pts1,
                                                    self.match_pts2,
                                                    cv2.FM_RANSAC, 0.1, 0.99)
            self.DL_F_Es() # need to be completed

        else:
            print("Method Error!")
            return 0

        self.FE = self.__get_max_norm_F(FE)

        return time_end - time_start

    def EstimateFMs(self, method='RANSAC'):
        """name
            description
        Args:
                    
                
        Returns:

        """
        F_temp = np.zeros((3,3))
        for i in range(self.camera_right.Image_num):
            self.ExactGoodMatch(index=i)
            self.EstimateFM(method=method)
            F_temp += self.FE
        
        F_temp /= self.camera_right.Image_num

        self.FE = F_temp
        
    def __pre_set(self):
        """name
            termination criteria
        Args:
            

        Returns:

        """
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        return criteria

    def __get_object_point(self):
        """name
            description
        Args:

        Returns:

        """
        objp = np.zeros((self.chess_board_size[0]*self.chess_board_size[1],3), np.float32)
        objp[:,:2] = np.mgrid[0:self.chess_board_size[1],0:self.chess_board_size[0]].T.reshape(-1,2)
        return objp

    def __find_corners(self, gray):
        """name
            description
        Args:

        Returns:
            ret: whether we find all the corners
        """
        ret, corners = cv2.findChessboardCorners(gray, (self.chess_board_size[1],self.chess_board_size[0]),None)
        return ret, corners

    def __find_corners_subpix(self, img, corners):
        """name
            description
        Args:

        Returns:
        """
        corners2 = cv2.cornerSubPix(img,corners,(15,15), (-1,-1), self.criteria)
        return corners2

    def __stereo_find_points(self):
        """
        """
        self.chess_board_size = np.array(CHESSBOARDSIZE)
        obj_pts = []
        img_pts_l = []
        img_pts_r = []

        if (self.camera_left.Image_num != self.camera_right.Image_num) and (self.camera_left.Image_num!=0):
            logging.error('Cameras have different images and cannot perform stereo calibration!')
            sys.exit('Error in log.')

        objp_temp = self.__get_object_point()
        self.criteria = self.__pre_set()
        
        logging.info('Finding object points...')
        for i in tqdm(range(self.camera_right.Image_num)):
            gray_l = self.camera_left.Image[i]
            gray_l = gray_l.astype(np.uint8)
            # print(gray_l.shape)
            assert len(gray_l.shape) == 2

            gray_r = self.camera_right.Image[i]
            gray_r = gray_r.astype(np.uint8)
            assert len(gray_r.shape) == 2
            
            ret_l, corners_temp_l = self.__find_corners(gray_l)
            ret_r, corners_temp_r = self.__find_corners(gray_r)

            if ret_l and ret_r:
                obj_pts.append(objp_temp)
                
                corners_l = self.__find_corners_subpix(gray_l, corners_temp_l)
                corners_r = self.__find_corners_subpix(gray_r, corners_temp_r)

                img_pts_l.append(corners_l)
                img_pts_r.append(corners_r)
        logging.info('Finding points done.')
        
        self.obj_pts = obj_pts
        self.img_pts_l = img_pts_l
        self.img_pts_r = img_pts_r
        self.stereo_pts_flag = True
        
    def stereo_calibration(self, write_yaml_flag=False, draw_flag=False, show_flag=False, save_flag=False, mono_calib=False, load_num=-1):
        """name
            if not calibrated:
                need to calibrate monocular camera first
            
            need to find points together

            cause we need the points
        Args:
                    
                
        Returns:

        """
        left_path = os.path.join(STEREOIMGPATH, 'left')
        right_path = os.path.join(STEREOIMGPATH, 'right')

        self.camera_left.load_images(left_path, 'Calibration', load_num=load_num)
        self.camera_right.load_images(right_path, 'Calibration', load_num=load_num)
        
        self.camera_left.chess_board_size = np.array(CHESSBOARDSIZE)
        self.camera_right.chess_board_size = np.array(CHESSBOARDSIZE)

        # find the points!
        if not self.stereo_pts_flag:
            self.__stereo_find_points()

        if mono_calib:
            # monocular calibration
            self.camera_left.calibrate_camera(draw_flag, show_flag, save_flag)
            self.camera_right.calibrate_camera(draw_flag, show_flag, save_flag)

            if write_yaml_flag:
                self.camera_left.write_yaml('_stereo_need')
                self.camera_right.write_yaml('_stereo_need')
        else:
            self.camera_left.IntP = cv2.initCameraMatrix2D(np.asarray(self.obj_pts), np.asarray(self.img_pts_l), tuple(self.camera_left.gary_img_shape), 0)
            self.camera_right.IntP = cv2.initCameraMatrix2D(np.asarray(self.obj_pts), np.asarray(self.img_pts_r), tuple(self.camera_left.gary_img_shape), 0)
        
        logging.info('Start Stereo Calibration')

        stereocalib_criteria = (cv2.TERM_CRITERIA_MAX_ITER+cv2.TERM_CRITERIA_EPS, 30, 1e-5)

        flags = 0
        flags |= cv2.CALIB_FIX_INTRINSIC
        # flags |= cv2.CALIB_FIX_PRINCIPAL_POINT
        # flags |= cv2.CALIB_USE_INTRINSIC_GUESS
        # flags |= cv2.CALIB_FIX_FOCAL_LENGTH
        # flags |= cv2.CALIB_FIX_ASPECT_RATIO
        # flags |= cv2.CALIB_ZERO_TANGENT_DIST
        # flags |= cv2.CALIB_RATIONAL_MODEL
        # flags |= cv2.CALIB_SAME_FOCAL_LENGTH
        # flags |= cv2.CALIB_FIX_K3
        # flags |= cv2.CALIB_FIX_K4
        # flags |= cv2.CALIB_FIX_K5

        self.stereo_calib_err, self.camera_left.IntP, self.camera_left.DisP, \
                                self.camera_right.IntP, self.camera_right.DisP, \
                                self.R_relate, self.t_relate, self.E_calib, self.F_calib = cv2.stereoCalibrate(
            self.obj_pts, self.img_pts_l, self.img_pts_r, 
            self.camera_left.IntP, self.camera_left.DisP, 
            self.camera_right.IntP, self.camera_right.DisP, 
            tuple(self.camera_left.gary_img_shape),
            criteria=stereocalib_criteria, flags=flags) 

        # self.stereo_calib_err, self.camera_left.IntP, self.camera_left.DisP, \
        #                         self.camera_right.IntP, self.camera_right.DisP, \
        #                         self.R_relate, self.t_relate, self.E_calib, self.F_calib = cv2.stereoCalibrate(
        #     self.obj_pts, self.img_pts_l, self.img_pts_r, 
        #     None,None,None,None,
        #     tuple(self.camera_left.gary_img_shape),
        #     None, None) 

        self.stereo_calib_flag = True
        logging.info('Stereo Calibration Done')

    def evaluate_F(self, whichF='est'):
        """name
            Evaluate Fundamental matrix
        Args:
                
        Returns:

        """

        F_dict = {
            'calib': self.F_calib,
            'GT': self.FM,
            'est': self.FE
        }
        F = F_dict[whichF]

        evaluator = Evaluator()
        evaluator.save_path = SAVEPATH
        evaluator.save_prefix = whichF+'_F_'

        

        if whichF == 'calib': # evaluate calibrated F
            if self.camera_left.Image_num == 0:
                self.cameras_load_imgs(STEREOIMGPATH, 'gray')

            if not self.stereo_pts_flag:
                self.__stereo_find_points()

            pts1 = np.zeros((self.camera_left.Image_num, self.chess_board_size[0]*self.chess_board_size[1], 2))
            pts2 = np.zeros((self.camera_left.Image_num, self.chess_board_size[0]*self.chess_board_size[1], 2))

            for i in range(self.camera_left.Image_num):
                pts1[i] = np.array(self.img_pts_l)[i,:,0,:] 
                pts2[i] = np.array(self.img_pts_r)[i,:,0,:] 

            pts1 = np.int32(pts1)
            pts2 = np.int32(pts2)
            print(pts1.shape)
            # sys.exit('N')
            evaluator.Evaluate_F(self.F_calib, pts1, pts2, self.camera_left.Image_num)
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          
    def write_yaml(self, postfix=''):
        """name
            description
        Args:
            
        Returns:
        """
        camera_model = {
            'Calibrated_F': self.F_calib.tolist(),
            'Estimated_F': self.FE.tolist(),
            'F_GT': self.FM.tolist(),
            'Calibrated_E': self.E_calib.tolist(),
            'Estimated_E': self.EE.tolist(),
            'R': self.R_relate.tolist(),
            't': self.t_relate.tolist(),
            'stereo_calib_err': self.stereo_calib_err,
            'stereo_calib_flag': self.stereo_calib_flag,
            'epipolar_constraint': self.epi_cons,
            'symmetric_epipolar_distance': self.sym_epi_dis,
            'epipolar_angle': self.epi_angle

        }
        yaml_file = os.path.join(WRITEPATH, 'Stereo_'+self.name+postfix+'.yaml')
        file = open(yaml_file, 'w', encoding='utf-8')
        yaml.dump(camera_model, file)
        file.close()
        logging.info('Write stereo camera model into '+yaml_file)

        

if __name__ == "__main__":

    log_init(LOGFILE)

    test = StereoCamera('HaiKang')


    # test.camera_left.load_images(os.path.join(STEREOIMGPATH,'left'), 'Calibration')
    # test.camera_right.load_images(os.path.join(STEREOIMGPATH,'right'), 'Calibration')

    # test.camera_left.calibrate_camera()
    # test.camera_left.evaluate_calibration()

    # test.camera_right.calibrate_camera()
    # test.camera_right.evaluate_calibration()

    # test.camera_left.write_yaml('_undistort_before_stereo')
    # test.camera_right.write_yaml('_undistort_before_stereo')

    # test.camera_left.init_by_config(os.path.join(CONFIGPATH,'camera_left.yaml'))
    # test.camera_right.init_by_config(os.path.join(CONFIGPATH,'camera_right.yaml'))

    test.stereo_calibration(draw_flag=False, save_flag=False, mono_calib=True, write_yaml_flag=False, load_num=10)


    # test.cameras_load_imgs(STEREOIMGPATH)
    # test.EstimateFMs()
    # test.init_stereo_by_config(os.path.join(CONFIGPATH, 'Stereo_cameraIntrin_guess.yaml'))

    # test.evaluate_F('calib')

    # test.camera_right.evaluate_calibration()
    # test.camera_left.evaluate_calibration()
    
    #================================
    # name format
    # _monoCalib_  _flag_ _intP_  
    #================================

    test.write_yaml('_monoCalib_flag_fix_IntP_load_10')
    # test.camera_left.write_yaml('')
    # test.camera_right.write_yaml('')
    #=====================================================Undistort
    # for i in range(test.camera_left.Image_num):
    #     test.camera_left.undistort(i, True)
    #     test.camera_right.undistort(i, True)
