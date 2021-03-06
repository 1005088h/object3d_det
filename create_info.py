from skimage import io
import pickle
import numpy as np
import os
from framework import box_np_ops
import copy

data_root = '/home/xy/ST/object3d_det/dataset/stels'

#dataset = 'ntu'
#dataset = 'soonlee' 
#dataset = 'mb1_hawpar'
#dataset = 'av1_ji'

#dataset = 'mb3_ji' 
#dataset = 'mb3_bb1'
dataset = 'mb3_cetran_syn_ped'
#dataset = 'mb3_bb2'



split = 'train'
#split = 'eval'


relative_path = os.path.join(dataset, split)
info_path = os.path.join(data_root, relative_path)

waymo = False
waymo_idx = [0, 1, 2, 3, 5, 6, 7]


def create_info(info_path, train_eval=True):
    filename = os.path.join(info_path, 'data_info.pkl')
    
    images_path = os.path.join(relative_path, 'image_2')
    points_path = os.path.join(relative_path, 'velodyne')
    calib_path = os.path.join(relative_path, 'calib')
    label_path = os.path.join(relative_path, 'label_2')
    
    if waymo:
        images_path = os.path.join(data_path, 'image_0')
        label_path = os.path.join(data_path, 'label_all')
    
    images = os.listdir(os.path.join(data_root, images_path))
    ids = [os.path.splitext(img)[0] for img in images]
    ids.sort()
    
    image_infos = []
    
    calib = True
    extend_matrix = True
    for id in ids:
        print('image_idx', id)
        image_info = {'image_idx': int(id), 'pointcloud_num_features': 4}
        image_info['img_path'] = os.path.join(images_path, id + '.jpg')
        image_info['img_shape'] = np.array(io.imread(os.path.join(data_root, image_info['img_path']) ).shape[:2], dtype=np.int32)
        image_info['velodyne_path'] = os.path.join(points_path, id + '.bin')
        
        image_info['calib/P0'] = None
        image_info['calib/P1'] = None
        image_info['calib/P2'] = None
        image_info['calib/P3'] = None
        image_info['calib/R0_rect'] = None 
        image_info['calib/Tr_velo_to_cam'] = None
        
        if calib:
            calib_file = os.path.join(data_root, calib_path, id + '.txt')
            with open(calib_file, 'r') as f:
                lines = f.readlines()
                if waymo:
                    lines = np.array(lines)
                    lines = lines[waymo_idx]
            
            P0 = np.array([float(info) for info in lines[0].split(' ')[1:13]]).reshape([3, 4])
            P1 = np.array([float(info) for info in lines[1].split(' ')[1:13]]).reshape([3, 4])
            P2 = np.array([float(info) for info in lines[2].split(' ')[1:13]]).reshape([3, 4])
            P3 = np.array([float(info) for info in lines[3].split(' ')[1:13]]).reshape([3, 4])
            if extend_matrix:
                P0 = _extend_matrix(P0)
                P1 = _extend_matrix(P1)
                P2 = _extend_matrix(P2)
                P3 = _extend_matrix(P3)
            image_info['calib/P0'] = P0
            image_info['calib/P1'] = P1
            image_info['calib/P2'] = P2
            image_info['calib/P3'] = P3
            R0_rect = np.array([float(info) for info in lines[4].split(' ')[1:10]]).reshape([3, 3])
            if extend_matrix:
                rect_4x4 = np.zeros([4, 4], dtype=R0_rect.dtype)
                rect_4x4[3, 3] = 1.
                rect_4x4[:3, :3] = R0_rect
            else:
                rect_4x4 = R0_rect
            image_info['calib/R0_rect'] = rect_4x4
            
            Tr_velo_to_cam = np.array([float(info) for info in lines[5].split(' ')[1:13]]).reshape([3, 4])
            Tr_imu_to_velo = np.array([float(info) for info in lines[6].split(' ')[1:13]]).reshape([3, 4])
            if extend_matrix:
                Tr_velo_to_cam = _extend_matrix(Tr_velo_to_cam)
                Tr_imu_to_velo = _extend_matrix(Tr_imu_to_velo)
            image_info['calib/Tr_velo_to_cam'] = Tr_velo_to_cam
            image_info['calib/Tr_imu_to_velo'] = Tr_imu_to_velo
            
        if train_eval:
            label_file = os.path.join(data_root, label_path, id + '.txt')
            image_info['annos'] = get_label_anno(label_file, rect_4x4, Tr_velo_to_cam)
            add_difficulty_to_annos_v2(image_info)
            
        image_infos.append(image_info)
        
    with open(filename, 'wb') as f:
        pickle.dump(image_infos, f) 
        
def get_label_anno(label_path, r_rect, velo2cam):
    annotations = {
        'name': [],
        'truncated': [],
        'occluded': [],
        'alpha': [],
        'bbox': [],
        'dimensions': [],
        'location': [],
        'rotation_y': []
    }
    with open(label_path, 'r') as f:
        lines = f.readlines()
        
    content = [line.strip().split(' ') for line in lines]
    num_objects = len([x[0] for x in content if x[0] != 'DontCare'])
    annotations['name'] = np.array([x[0] for x in content], dtype = 'U15')
    num_gt = len(annotations['name'])
    annotations['truncated'] = np.array([float(x[1]) for x in content])
    annotations['occluded'] = np.array([int(x[2]) for x in content])
    annotations['alpha'] = np.array([float(x[3]) for x in content])
    annotations['bbox'] = np.array([[float(info) for info in x[4:8]] for x in content]).reshape(-1, 4)
    # dimensions will convert hwl format to standard lwh(lidar) format.
    annotations['dimensions'] = np.array([[float(info) for info in x[8:11]] for x in content]).reshape(-1, 3)[:, [2, 1, 0]]
        
    xyz = np.array([[float(info) for info in x[11:14]] for x in content]).reshape(-1, 3)
    if not waymo:
        xyz = box_np_ops.camera_to_lidar(xyz, r_rect, velo2cam)
    annotations['location'] = xyz
   
    rotation_y = np.array([1.5 * np.pi - float(x[14]) for x in content]).reshape(-1)
    annotations['rotation_y'] = box_np_ops.limit_period(rotation_y, period=2.0 * np.pi)
    if waymo:
        annotations['rotation_y'] = np.array([float(x[14]) for x in content]).reshape(-1)
    #print('rotation_y_2', annotations['rotation_y'])
    if len(content) != 0 and len(content[0]) == 16:  # have score
        annotations['score'] = np.array([float(x[15]) for x in content])
    else:
        annotations['score'] = np.zeros((annotations['bbox'].shape[0], ))
    index = list(range(num_objects)) + [-1] * (num_gt - num_objects)
    annotations['index'] = np.array(index, dtype=np.int32)
    annotations['group_ids'] = np.arange(num_gt, dtype=np.int32)
    return annotations
    
def add_difficulty_to_annos_v2(info):
    v_path = info['velodyne_path']
    num_features = info['pointcloud_num_features']
    points = np.fromfile(os.path.join(data_root, v_path), dtype=np.float32, count=-1).reshape([-1, num_features])
    
    annos = info['annos']
    dims = annos['dimensions']  # lwh format
    loc = annos['location'] # xyz in camera
    rots = annos['rotation_y'] # rad in camera
            
    rect = info['calib/R0_rect']
    Trv2c = info['calib/Tr_velo_to_cam']
        
    gt_boxes_lidar = np.concatenate([loc, dims, rots[..., np.newaxis]], axis=1)
    
    gt_boxes = copy.deepcopy(gt_boxes_lidar)
    gt_point_table = box_np_ops.points_in_rbbox(points, gt_boxes)
    gt_point_count = gt_point_table.sum(axis=0) 
    annos["num_points"] = gt_point_count
    
    gt_boxes = copy.deepcopy(gt_boxes_lidar)
    gt_boxes[:, 3:6] = gt_boxes[:, 3:6] + np.array([1.2, 0.5, 8])
    gt_point_table = box_np_ops.points_in_rbbox(points, gt_boxes)
    gt_point_count = gt_point_table.sum(axis=0)
    annos["difficulty"] = gt_point_count



def add_difficulty_to_annos(info):
    min_height = [40, 25, 25]  # minimum height for evaluated groundtruth/detections
    max_occlusion = [0, 1, 2]  # maximum occlusion level of the groundtruth used for evaluation
    max_trunc = [ 0.15, 0.3, 0.5]  # maximum truncation level of the groundtruth used for evaluation
    annos = info['annos']
    dims = annos['dimensions']  # lhw format
    bbox = annos['bbox']
    height = bbox[:, 3] - bbox[:, 1]
    occlusion = annos['occluded']
    truncation = annos['truncated']
    diff = []
    easy_mask = np.ones((len(dims), ), dtype=np.bool)
    moderate_mask = np.ones((len(dims), ), dtype=np.bool)
    hard_mask = np.ones((len(dims), ), dtype=np.bool)
    i = 0
    for h, o, t in zip(height, occlusion, truncation):
        if o > max_occlusion[0] or h <= min_height[0] or t > max_trunc[0]:
            easy_mask[i] = False
        if o > max_occlusion[1] or h <= min_height[1] or t > max_trunc[1]:
            moderate_mask[i] = False
        if o > max_occlusion[2] or h <= min_height[2] or t > max_trunc[2]:
            hard_mask[i] = False
        i += 1
    is_easy = easy_mask
    is_moderate = np.logical_xor(easy_mask, moderate_mask)
    is_hard = np.logical_xor(hard_mask, moderate_mask)

    for i in range(len(dims)):
        if is_easy[i]:
            diff.append(0)
        elif is_moderate[i]:
            diff.append(1)
        elif is_hard[i]:
            diff.append(2)
        else:
            diff.append(-1)
    annos["difficulty"] = np.array(diff, np.int32)
    return diff
    
def _extend_matrix(mat):
    mat = np.concatenate([mat, np.array([[0., 0., 0., 1.]])], axis=0)
    return mat
        
        
if __name__ == '__main__':
   
    create_info(info_path)
    #fire.Fire()       
         
