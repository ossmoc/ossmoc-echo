"""Conservative horizontal letterbox detection; no screen/DPI assumptions."""
import numpy as np


def letterbox_bounds(image):
    width,height=image.size
    full=(0,0,width,height)
    if width<320 or height<240:
        return full
    # Ignore corner controls but require virtually uniform true-black bars.
    rgb=image.convert('RGB')
    pixels=np.asarray(rgb)[:,width//4:3*width//4:8]
    black=(pixels.max(axis=2)<=3).mean(axis=1)>=.999
    active=np.flatnonzero(~black)
    if not len(active):
        return full
    top,bottom=int(active[0]),int(active[-1])+1
    lower=height-bottom
    if (not height*.035<=top<=height*.24 or not height*.035<=lower<=height*.24
            or abs(top-lower)>max(3,height*.004) or bottom-top<height*.55):
        return full
    # A bright, textured hard edge across most of the image is needed on both
    # sides. A dark/faded scene, subtitles on black or a central light is not
    # sufficient evidence to crop the capture.
    for edge in (top,bottom-1):
        row=np.asarray(rgb.crop((width//12,edge,11*width//12,edge+1)))
        values=row.max(axis=2)
        if (values>18).mean()<.65 or row[0].astype('int16').std(axis=0).max()<6:
            return full
    return 0,top,width,bottom-top


class ViewportTracker:
    def __init__(self):
        self.pending=None
        self.count=0

    def update(self,image):
        candidate=letterbox_bounds(image)
        if candidate==self.pending:
            self.count+=1
        else:
            self.pending=candidate
            self.count=1
        full=(0,0,*image.size)
        return candidate if candidate==full or self.count>=2 else full
