import numpy as np
import framework.box_np_ops as box_np_ops
import framework.box_torch_ops as box_torch_ops

class AnchorAssigner:
    def __init__(self, config):
        self._sizes = config['anchor_sizes']
        self._feature_map_size = np.array(config['feature_map_size'], dtype=np.float32)
        self._anchor_strides = config['detection_range_diff'] / self._feature_map_size
        self._anchor_offsets = config['detection_offset']
        self._rotations = config['rotations']
        self._class_id = config['class_id']
        self._grid_size = config['grid_size']
        self.box_code_size = config['box_code_size']
        matched_threshold = config['matched_threshold']
        unmatched_threshold = config['unmatched_threshold']

        self.anchors = self.generate().reshape([-1, 7])
        self.num_anchors = self.anchors.shape[0]
        self.matched_threshold = np.full(self.num_anchors, matched_threshold, self.anchors.dtype)
        self.unmatched_threshold = np.full(self.num_anchors, unmatched_threshold, self.anchors.dtype)
        self.anchors_bv = box_np_ops.rbbox2d_to_near_bbox(self.anchors[:, [0, 1, 3, 4, 6]])

    def generate(self):
        x_stride, y_stride, z_stride = self._anchor_strides
        x_offset, y_offset, z_offset = self._anchor_offsets + self._anchor_strides / 2

        x_centers = np.arange(self._feature_map_size[0], dtype=np.float32)
        y_centers = np.arange(self._feature_map_size[1], dtype=np.float32)
        z_centers = np.arange(self._feature_map_size[2], dtype=np.float32)

        x_centers = x_centers * x_stride + x_offset
        y_centers = y_centers * y_stride + y_offset
        z_centers = z_centers * z_stride + z_offset

        sizes = np.reshape(np.array(self._sizes, dtype=np.float32), [-1, 3])
        rotations = np.array(self._rotations, dtype=np.float32)
        rets = np.meshgrid(x_centers, y_centers, z_centers, rotations, indexing='ij')
        tile_shape = [1] * 5
        tile_shape[-2] = int(sizes.shape[0])
        for i in range(len(rets)):
            rets[i] = rets[i][..., np.newaxis]
        tile_size_shape = list(rets[0].shape)
        tile_size_shape[4] = 1
        sizes = np.tile(sizes, tile_size_shape)
        rets.insert(3, sizes)
        ret = np.concatenate(rets, axis=-1)
        return ret

    def create_mask(self, coors, grid_size, voxel_size, offset):
        anchors_bv = self.anchors_bv
        dense_voxel_map = box_np_ops.sparse_sum_for_anchors_mask(coors, tuple(grid_size[:-1]))
        dense_voxel_map = dense_voxel_map.cumsum(0)
        dense_voxel_map = dense_voxel_map.cumsum(1)
        anchors_area = box_np_ops.fused_get_anchors_area(dense_voxel_map, anchors_bv, voxel_size, offset, grid_size)
        anchors_mask = anchors_area > 0
        #anchors_mask = anchors_mask.astype(np.uint8)
        return anchors_mask

    def assign(self, gt_classes, gt_boxes, anchors_mask):
        inds_inside = np.where(anchors_mask)[0]
        anchors = self.anchors[inds_inside, :]
        matched_threshold = self.matched_threshold[inds_inside]
        unmatched_threshold = self.unmatched_threshold[inds_inside]
        num_inside = len(inds_inside)

        labels = -np.ones((num_inside,), dtype=np.int32)
        bbox_targets = np.zeros((num_inside, self.box_code_size), dtype=self.anchors.dtype)

        if len(gt_boxes) > 0 and anchors.shape[0] > 0:
            # Compute overlaps between the anchors and the gt boxes overlaps
            anchor_by_gt_overlap = similarity_fn(anchors, gt_boxes)
            # Map from anchor to gt box that has highest overlap
            anchor_to_gt_argmax = anchor_by_gt_overlap.argmax(axis=1)
            # For each anchor, amount of overlap with most overlapping gt box
            anchor_to_gt_max = anchor_by_gt_overlap[np.arange(num_inside), anchor_to_gt_argmax]
            # Map from gt box to an anchor that has highest overlap
            gt_to_anchor_argmax = anchor_by_gt_overlap.argmax(axis=0)
            # For each gt box, amount of overlap with most overlapping anchor
            gt_to_anchor_max = anchor_by_gt_overlap[gt_to_anchor_argmax, np.arange(anchor_by_gt_overlap.shape[1])]
            # must remove gt which doesn't match any anchor.
            empty_gt_mask = gt_to_anchor_max == 0
            gt_to_anchor_max[empty_gt_mask] = -1
            # Find all anchors that share the max overlap amount
            # (this includes many ties)
            anchors_with_max_overlap = np.where(anchor_by_gt_overlap == gt_to_anchor_max)[0]
            # Fg label: for each gt use anchors with highest overlap
            # (including ties)
            gt_inds_force = anchor_to_gt_argmax[anchors_with_max_overlap]
            labels[anchors_with_max_overlap] = gt_classes[gt_inds_force]

            # Fg label: above threshold IOU
            pos_inds = anchor_to_gt_max >= matched_threshold
            gt_inds = anchor_to_gt_argmax[pos_inds]
            labels[pos_inds] = gt_classes[gt_inds]

            # Bg label: below threshold IOU
            bg_inds = np.where(anchor_to_gt_max < unmatched_threshold)[0]
            labels[bg_inds] = 0

            # Re-assign max overlap gt if all below threshold IOU
            labels[anchors_with_max_overlap] = gt_classes[gt_inds_force]

            fg_inds = np.where(labels > 0)[0]
            bbox_targets[fg_inds, :] = box_np_ops.box_encode(gt_boxes[anchor_to_gt_argmax[fg_inds], :],
                                                             anchors[fg_inds, :])
        else:
            labels[:] = 0

        bbox_outside_weights = np.zeros((num_inside,), dtype=self.anchors.dtype)
        bbox_outside_weights[labels > 0] = 1.0

        dir_cls_targets = None
        # Map up to original set of anchors
        if inds_inside is not None:
            labels = unmap(labels, self.num_anchors, inds_inside, fill=-1)
            bbox_targets = unmap(bbox_targets, self.num_anchors, inds_inside, fill=0)
            bbox_outside_weights = unmap(bbox_outside_weights, self.num_anchors, inds_inside, fill=0)
            dir_cls_targets = get_direction_target(self.anchors, bbox_targets)

        return labels, bbox_targets, bbox_outside_weights, dir_cls_targets


def similarity_fn(anchors, gt_boxes):
    anchors_rbv = anchors[:, [0, 1, 3, 4, 6]]
    gt_boxes_rbv = gt_boxes[:, [0, 1, 3, 4, 6]]
    boxes1_bv = box_np_ops.rbbox2d_to_near_bbox(anchors_rbv)
    boxes2_bv = box_np_ops.rbbox2d_to_near_bbox(gt_boxes_rbv)
    ret = box_np_ops.iou_jit(boxes1_bv, boxes2_bv, eps=0.0)
    return ret


def unmap(data, count, inds, fill=0):
    """Unmap a subset of item (data) back to the original set of items (of
    size count)"""
    if count == len(inds):
        return data

    if len(data.shape) == 1:
        ret = np.empty((count,), dtype=data.dtype)
        ret.fill(fill)
        ret[inds] = data
    else:
        ret = np.empty((count,) + data.shape[1:], dtype=data.dtype)
        ret.fill(fill)
        ret[inds, :] = data
    return ret


def get_direction_target(anchors, reg_targets):
    rot_gt = reg_targets[..., -1] + anchors[..., -1]
    dir_cls_targets = rot_gt > 0
    return dir_cls_targets.astype('int32')
