from .models.experimental import attempt_load

class YOLOv4:
    def __init__(self,cfgfile,weightfile,namesfile) -> None:
        self.model=attempt_load(weightfile,cfgfile.device)
        self.conf_thresh=cfgfile.conf_thres
        self.nms_thresh=cfgfile.iou.thres
        
    def __call__(self, ori_img):
        pass
    def load_class_names(self,namesfile):
        pass