import subprocess
import shlex
import sys
import logging
import os
import datetime
import math
import glob
import pipes
from dateutil import relativedelta

##################################
# Generate tooltip thumbnail images & corresponding WebVTT file for a video (e.g MP4).
# Final product is one *_sprite.jpg file and one *_thumbs.vtt file.
#
# DEPENDENCIES: required: ffmpeg & imagemagick
#               optional: sips (comes with MacOSX) - yields slightly smaller sprites
#    download ImageMagick: http://www.imagemagick.org/script/index.php OR http://www.imagemagick.org/script/binary-releases.php (on MacOSX: "sudo port install ImageMagick")
#    download ffmpeg: http://www.ffmpeg.org/download.html
#
# TESTING NOTES: Tested putting time gaps between thumbnail segments, but had no visual effect in JWplayer, so omitted.
#                Tested using an offset so that thumbnail would show what would display mid-way through clip rather than for the 1st second of the clip, but was not an improvement.
##################################

# TODO determine optimal number of images/segment distance based on length of video? (so longer videos don't have huge sprites)

USE_SIPS = False  # True to use sips if using MacOSX (creates slightly smaller sprites), else set to False to use ImageMagick
THUMB_RATE_SECONDS = 10  # every Nth second take a snapshot
THUMB_WIDTH = 160  # 100-150 is recommended width; I like smaller files
MAX_GRID_SIZE = 6  # Single sprite max grid size
SKIP_FIRST = True  # True to skip a thumbnail of second 1; often not a useful image, plus user knows beginning without needing preview
SPRITE_NAME = "sprite.jpg"  # jpg is much smaller than png, so using jpg
VTTFILE_NAME = "thumbs.vtt"
THUMB_OUTDIR = "thumbs"
USE_UNIQUE_OUTDIR = False  # true to make a unique timestamped output dir each time, else False to overwrite/replace existing outdir
TIMESYNC_ADJUST = -.5  # set to 1 to not adjust time (gets multiplied by thumbRate); On my machine,ffmpeg snapshots show earlier images than expected timestamp by about 1/2 the thumbRate (for one vid, 10s thumbrate->images were 6s earlier than expected;45->22s early,90->44 sec early)
logger = logging.getLogger(sys.argv[0])
logSetup = False


class SpriteTask:
    """small wrapper class as convenience accessor for external scripts"""

    def __init__(self, video_file):
        self.remote_file = video_file.startswith("http")
        if not self.remote_file and not os.path.exists(video_file):
            sys.exit("File does not exist: %s" % video_file)
        base_file = os.path.basename(video_file)
        base_file_no_speed = remove_speed(base_file)  # strip trailing speed suffix from file/dir names, if present
        new_out_dir = makeOutDir(base_file_no_speed)
        file_prefix, ext = os.path.splitext(base_file_no_speed)
        sprite_file = os.path.join(new_out_dir, "%s_%s" % (file_prefix, SPRITE_NAME))
        vtt_file = os.path.join(new_out_dir, "%s_%s" % (file_prefix, VTTFILE_NAME))
        self.video_file = video_file
        self.vtt_file = vtt_file
        self.sprite_file = sprite_file
        self.out_dir = new_out_dir

    def getVideoFile(self):
        return self.video_file

    def getOutDir(self):
        return self.out_dir

    def getSpriteFile(self):
        return self.sprite_file

    def getVTTFile(self):
        return self.vtt_file


def makeOutDir(video_file):
    """create unique output dir based on video file name and current timestamp"""
    base, ext = os.path.splitext(video_file)
    script = sys.argv[0]
    base_path = os.path.dirname(
        os.path.abspath(script))  # make output dir always relative to this script regardless of shell directory
    if len(THUMB_OUTDIR) > 0 and THUMB_OUTDIR[0] == '/':
        output_dir = THUMB_OUTDIR
    else:
        output_dir = os.path.join(base_path, THUMB_OUTDIR)
    if USE_UNIQUE_OUTDIR:
        new_out_dir = "%s.%s" % (os.path.join(output_dir, base), datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    else:
        new_out_dir = "%s_%s" % (os.path.join(output_dir, base), "vtt")
    if not os.path.exists(new_out_dir):
        logger.info("Making dir: %s" % new_out_dir)
        os.makedirs(new_out_dir)
    elif os.path.exists(new_out_dir) and not USE_UNIQUE_OUTDIR:
        # remove previous contents if reusing out dir
        files = os.listdir(new_out_dir)
        print("Removing previous contents of output directory: %s" % new_out_dir)
        for f in files:
            os.unlink(os.path.join(new_out_dir, f))
    return new_out_dir


def doCmd(cmd, logger=logger):  # execute a shell command and return/print its output
    logger.info("START [%s] : %s " % (datetime.datetime.now(), cmd))
    args = shlex.split(cmd)  # tokenize args
    output = None
    try:
        output = subprocess.check_output(args, stderr=subprocess.STDOUT)  # pipe stderr into stdout
    except Exception as e:
        ret = "ERROR   [%s] An exception occurred\n%s\n%s" % (datetime.datetime.now(), output, str(e))
        logger.error(ret)
        raise e
    ret = "END   [%s]\n%s" % (datetime.datetime.now(), output)
    logger.info(ret)
    sys.stdout.flush()
    return output


def take_snaps(video_file, newoutdir, thumb_rate=None):
    """
    take snapshot image of video every Nth second and output to sequence file names and custom directory
        reference: https://trac.ffmpeg.org/wiki/Create%20a%20thumbnail%20image%20every%20X%20seconds%20of%20the%20video
    """
    if not thumb_rate:
        thumb_rate = THUMB_RATE_SECONDS
    rate = "1/%d" % thumb_rate  # 1/60=1 per minute, 1/120=1 every 2 minutes
    cmd = "ffmpeg -i %s -f image2 -bt 20M -vf fps=%s -aspect 16:9 %s/tv%%05d.jpg" % (
        pipes.quote(video_file), rate, pipes.quote(newoutdir))
    doCmd(cmd)
    if SKIP_FIRST:
        # remove the first image
        logger.info("Removing first image, unneeded")
        os.unlink("%s/tv00001.jpg" % newoutdir)
    count = len(os.listdir(newoutdir))
    logger.info("%d thumbs written in %s" % (count, newoutdir))
    # return the list of generated files
    return count, get_thumb_images(newoutdir)


def get_thumb_images(newdir):
    return glob.glob("%s/tv*.jpg" % newdir)


def optimize_sprites_optipng(files):
    for file in files:
        cmd = "optipng %s" % (pipes.quote(file))
        doCmd(cmd)


def optimize_sprites_jpegoptim(files, factor):
    if factor:
        for file in files:
            cmd = "jpegoptim -m %s %s" % (factor, pipes.quote(file))
            doCmd(cmd)
    else:
        for file in files:
            cmd = "jpegoptim %s" % (pipes.quote(file))
            doCmd(cmd)


def get_sprite_images(sprite_file):
    files_base_name, extension = os.path.splitext(sprite_file)
    return glob.glob("%s*%s" % (files_base_name, extension))


def resize(files):
    """change image output size to 100 width (originally matches size of video)
      - pass a list of files as string rather than use '*' with sips command because
        subprocess does not treat * as wildcard like shell does"""
    if USE_SIPS:
        # HERE IS MAC SPECIFIC PROGRAM THAT YIELDS SLIGHTLY SMALLER JPGs
        doCmd("sips --resampleWidth %d %s" % (THUMB_WIDTH, " ".join(map(pipes.quote, files))))
    else:
        # THIS COMMAND WORKS FINE TOO AND COMES WITH IMAGEMAGICK, IF NOT USING A MAC
        doCmd("mogrify -geometry %dx %s" % (THUMB_WIDTH, " ".join(map(pipes.quote, files))))


def get_geometry(file):
    """execute command to give geometry HxW+X+Y of each file matching command
       identify -format "%g - %f\n" *         #all files
       identify -format "%g - %f\n" onefile.jpg  #one file
     SAMPLE OUTPUT
        100x66+0+0 - _tv001.jpg
        100x2772+0+0 - sprite2.jpg
        4200x66+0+0 - sprite2h.jpg"""
    geom = doCmd("""identify -format "%%g - %%f\n" %s""" % pipes.quote(file))
    parts = geom.decode().split("-", 1)
    return parts[0].strip()  # return just the geometry prefix of the line, sans extra whitespace


def make_vtt(sprite_files, num_segments, coords, grid_size, writefile, thumb_rate=None):
    """generate & write vtt file mapping video time to each image's coordinates
    in our spritemap"""
    if not thumb_rate:
        thumb_rate = THUMB_RATE_SECONDS
    wh, xy = coords.split("+", 1)
    w, h = wh.split("x")
    w = int(w)
    h = int(h)

    vtt = ["WEBVTT", ""]  # line buffer for file contents
    if SKIP_FIRST:
        clipstart = thumb_rate  # offset time to skip the first image
    else:
        clipstart = 0

    clipend = clipstart + thumb_rate
    adjust = thumb_rate * TIMESYNC_ADJUST

    sprites_count = len(sprite_files)

    if sprites_count == 1:
        base_file = os.path.basename(sprite_files[0])
        for img_num in range(1, num_segments + 1):
            xywh = get_grid_coordinates(img_num - 1, grid_size, w, h)
            start = get_time_str(clipstart, adjust=adjust)
            end = get_time_str(clipend, adjust=adjust)
            clipstart = clipend
            clipend += thumb_rate
            # vtt.append("Img %d" % img_num)
            vtt.append("%s --> %s" % (start, end))  # 00:00.000 --> 00:05.000
            vtt.append("%s#xywh=%s" % (base_file, xywh))
            vtt.append("")  # Linebreak
    else:
        for img_num in range(1, num_segments + 1):
            xywh = get_grid_coordinates(img_num - 1, grid_size, w, h)
            start = get_time_str(clipstart, adjust=adjust)
            end = get_time_str(clipend, adjust=adjust)
            clipstart = clipend
            clipend += thumb_rate
            # vtt.append("Img %d" % img_num)
            vtt.append("%s --> %s" % (start, end))  # 00:00.000 --> 00:05.000
            file_index = math.floor(img_num/(grid_size**2))
            base_file = os.path.basename(sprite_files[file_index])
            vtt.append("%s#xywh=%s" % (base_file, xywh))
            vtt.append("")  # Linebreak

    vtt = "\n".join(vtt)
    # output to file
    write_vtt(writefile, vtt)


def get_time_str(numseconds, adjust=None):
    """ convert time in seconds to VTT format time (HH:)MM:SS.ddd"""
    if adjust:  # offset the time by the adjust amount, if applicable
        seconds = max(numseconds + adjust, 0)  # don't go below 0! can't have a negative timestamp
    else:
        seconds = numseconds
    delta = relativedelta.relativedelta(seconds=seconds)
    return "%02d:%02d:%02d.000" % (delta.hours, delta.minutes, delta.seconds)


# def get_grid_coordinates(imgnum, gridsize, w, h):
#     """ given an image number in our sprite, map the coordinates to it in X,Y,W,H format"""
#     y = (imgnum - 1) / gridsize
#     x = (imgnum - 1) - (y * gridsize)
#     imgx = x * w
#     imgy = y * h
#     return "%s,%s,%s,%s" % (imgx, imgy, w, h)

def get_grid_coordinates(img_num, grid_size, w, h):
    """ given an image number in our sprite, map the coordinates to it in X,Y,W,H format"""
    y = int(img_num / grid_size)
    x = int(img_num - (y * grid_size))
    img_x = x * w
    img_y = y * h
    return "%s,%s,%s,%s" % (img_x, img_y, w, h)


def makesprite(outdir, spritefile, coords, gridsize):
    """montage _tv*.jpg -tile 8x8 -geometry 100x66+0+0 montage.jpg  #GRID of images
           NOT USING: convert tv*.jpg -append sprite.jpg     #SINGLE VERTICAL LINE of images
           NOT USING: convert tv*.jpg +append sprite.jpg     #SINGLE HORIZONTAL LINE of images
     base the sprite size on the number of thumbs we need to make into a grid."""
    grid = "%dx%d" % (gridsize, gridsize)
    cmd = "montage -background transparent %s/tv*.jpg -tile %s -geometry %s %s" % (pipes.quote(outdir), grid, coords, pipes.quote(spritefile))
    doCmd(cmd)


def write_vtt(vtt_file, contents):
    """ output VTT file """
    with open(vtt_file, mode="w") as file:
        file.write(contents)
    logger.info("Wrote: %s" % vtt_file)


def remove_speed(video_file):
    """some of my files are suffixed with datarate, e.g. myfile_3200.mp4;
     this trims the speed from the name since it's irrelevant to my sprite names (which apply regardless of speed);
     you won't need this if it's not relevant to your filenames"""
    video_file = video_file.strip()
    speed = video_file.rfind("_")
    speed_last = video_file.rfind(".")
    maybe_speed = video_file[speed + 1:speed_last]
    try:
        int(maybe_speed)
        video_file = video_file[:speed] + video_file[speed_last:]
    except:
        pass
    return video_file


def remove_old_thumb_files(files):
    for file in files:
        os.remove(file)


def run(task, thumbRate=None):
    # addLogging()
    if not thumbRate:
        thumbRate = THUMB_RATE_SECONDS

    out_dir = task.getOutDir()
    spritefile = task.getSpriteFile()

    # create snapshots
    numfiles, thumbfiles = take_snaps(task.getVideoFile(), out_dir, thumb_rate=thumbRate)

    # resize them to be mini
    resize(thumbfiles)

    # get coordinates from a resized file to use in spritemapping]
    if numfiles <= MAX_GRID_SIZE**2:
        gridsize = int(math.ceil(math.sqrt(numfiles)))
    else:
        gridsize = MAX_GRID_SIZE

    coords = get_geometry(thumbfiles[0])  # use the first file (since they are all same size) to get geometry settings

    # first_elem = 1
    # last_elem = MAX_GRID_SIZE ** 2
    # l = 0
    #
    # for i in range(0, numfiles, MAX_GRID_SIZE**2):
    #     l += 1
    #     if numfiles <= last_elem:
    #         last_elem = numfiles
    #         if last_elem == first_elem:
    #             # print(first_elem)
    #             break
    #         print(first_elem, '->', last_elem)
    #         grid = last_elem - first_elem + 1
    #         spritefile2 = os.path.join(out_dir, "%02d%s" % (l, SPRITE_NAME))
    #         gridsize1 = int(math.ceil(math.sqrt(grid)))
    #         print(gridsize1)
    #         makesprite(out_dir, spritefile2, coords, gridsize1)
    #         break
    #     print(first_elem, '->', last_elem)
    #     first_elem = last_elem + 1
    #     last_elem += MAX_GRID_SIZE**2
    #     grid = last_elem - first_elem + 1
    #     print(grid)
    #     spritefile2 = os.path.join(out_dir, "%02d%s" % (l, SPRITE_NAME))
    #     gridsize1 = int(math.ceil(math.sqrt(grid)))
    #     makesprite(out_dir, spritefile2, coords, gridsize1)

    # convert small files into a single sprite grid
    makesprite(out_dir, spritefile, coords, gridsize)

    sprites_array = get_sprite_images(spritefile)
    sprites_array.sort()

    # optimize_sprites_optipng(sprites_array)         # Just optimize
    # optimize_sprites_jpegoptim(sprites_array, 50)   # Force file compression
    optimize_sprites_jpegoptim(sprites_array, False)  # Just optimize

    # Remove unneeded thumb files
    remove_old_thumb_files(thumbfiles)

    # generate a vtt with coordinates to each image in sprite
    make_vtt(sprites_array, numfiles, coords, gridsize, task.getVTTFile(), thumb_rate=thumbRate)


def addLogging():
    global logSetup
    if not logSetup:
        basescript = os.path.splitext(os.path.basename(sys.argv[0]))[0]
        LOG_FILENAME = 'logs/%s.%s.log' % (basescript, datetime.datetime.now().strftime(
            "%Y%m%d_%H%M%S"))  # new log per job so we can run this program concurrently
        # CONSOLE AND FILE LOGGING
        print("Writing log to: %s" % LOG_FILENAME)
        if not os.path.exists('logs'):
            os.makedirs('logs')
        logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(LOG_FILENAME)
        logger.addHandler(handler)
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        logger.addHandler(ch)
        logSetup = True  # set flag so we don't reset log in same batch


if __name__ == "__main__":
    if not len(sys.argv) > 1:
        sys.exit("Please pass the full path or url to the video file for which to create thumbnails.")
    if len(sys.argv) == 3:
        THUMB_OUTDIR = sys.argv[2]
    videofile = sys.argv[1]
    task = SpriteTask(videofile)
    run(task)
