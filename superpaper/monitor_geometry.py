"""Canvas sizing, monitor crop geometry, PPI normalization and bezel coordinates.

These calculations have no dependency on the wx GUI, desktop wallpaper setter
or mutable display-detection globals.
"""

from operator import itemgetter

import superpaper.sp_logging as sp_logging


def compute_canvas(res_array, offset_array):
    """Computes the size of the total desktop area from monitor resolutions and offsets."""
    # Take the subtractions of right-most right - left-most left
    # and bottom-most bottom - top-most top (=0).
    leftmost = 0
    topmost = 0
    right_edges = []
    bottom_edges = []
    for res, off in zip(res_array, offset_array):
        right_edges.append(off[0] + res[0])
        bottom_edges.append(off[1] + res[1])
    # Right-most edge.
    rightmost = max(right_edges)
    # Bottom-most edge.
    bottommost = max(bottom_edges)
    canvas_size = [rightmost - leftmost, bottommost - topmost]
    if sp_logging.DEBUG:
        sp_logging.G_LOGGER.info("Canvas size: %s", canvas_size)
    return canvas_size


def compute_ppi_corrected_res_array(res_array, ppi_list_rel_density):
    """Return ppi density normalized sizes of the real resolutions."""
    eff_res_array = []
    for i in range(len(res_array)):
        effw = round(res_array[i][0] / ppi_list_rel_density[i])
        effh = round(res_array[i][1] / ppi_list_rel_density[i])
        eff_res_array.append((effw, effh))
    return eff_res_array


def get_center(res):
    """Computes center point of a resolution rectangle."""
    return (round(res[0] / 2), round(res[1] / 2))


def get_all_centers(resarr_eff, manual_offsets):
    """Computes center points of given resolution list taking into account their offsets."""
    centers = []
    sum_widths = 0
    # get the vertical pixel distance of the center of the left most display
    # from the top.
    center_standard_height = get_center(resarr_eff[0])[1]
    if len(manual_offsets) < len(resarr_eff):
        sp_logging.G_LOGGER.info(
            "get_all_centers: Not enough manual offsets: \
                                 %s for displays: %s",
            len(manual_offsets),
            len(resarr_eff),
        )
    else:
        for i in range(len(resarr_eff)):
            horiz_radius = get_horizontal_radius(resarr_eff[i])
            # here take the center height to be the same for all the displays
            # unless modified with the manual offset
            center_pos_from_anchor_left_top = (
                sum_widths + manual_offsets[i][0] + horiz_radius,
                center_standard_height + manual_offsets[i][1],
            )
            centers.append(center_pos_from_anchor_left_top)
            sum_widths += resarr_eff[i][0]
    if sp_logging.DEBUG:
        sp_logging.G_LOGGER.info("centers: %s", centers)
    return centers


def get_lefttop_from_center(center, res):
    """Compute top left coordinate of a rectangle from its center."""
    return (center[0] - round(res[0] / 2), center[1] - round(res[1] / 2))


def get_rightbottom_from_lefttop(lefttop, res):
    """Compute right bottom corner of a rectangle from its left top."""
    return (lefttop[0] + res[0], lefttop[1] + res[1])


def get_horizontal_radius(res):
    """Returns half the width of the input rectangle."""
    return round(res[0] / 2)


def compute_crop_tuples(resolution_array_ppinormalized, manual_offsets):
    # Assume the centers of the physical displays are aligned on common
    # horizontal line. If this is not the case one must use the manual
    # offsets defined in the profile for adjustment (and bezel corrections).
    # Anchor positions to the top left corner of the left most display. If
    # its size is scaled up, one will need to adjust the horizontal positions
    # of all the displays. (This is automatically handled by using the
    # effective resolution array).
    # Additionally one must make sure that the highest point of the display
    # arrangement is at y=0.
    crop_tuples = []
    centers = get_all_centers(resolution_array_ppinormalized, manual_offsets)
    for center, res in zip(centers, resolution_array_ppinormalized):
        lefttop = get_lefttop_from_center(center, res)
        rightbottom = get_rightbottom_from_lefttop(lefttop, res)
        crop_tuples.append(lefttop + rightbottom)
    # Translate crops so that the highest point is at y=0 -- remember to add
    # translation to both top and bottom coordinates! Same horizontally.
    # Left-most edge of the crop tuples.
    leftmost = min(crop_tuples, key=itemgetter(0))[0]
    # Top-most edge of the crop tuples.
    topmost = min(crop_tuples, key=itemgetter(1))[1]
    if leftmost == 0 and topmost == 0:
        if sp_logging.DEBUG:
            sp_logging.G_LOGGER.info("crop_tuples: %s", crop_tuples)
        return crop_tuples  # [(left, up, right, bottom),...]
    else:
        crop_tuples_translated = translate_crops(crop_tuples, (leftmost, topmost))
        if sp_logging.DEBUG:
            sp_logging.G_LOGGER.info("crop_tuples_translated: %s", crop_tuples_translated)
        return crop_tuples_translated  # [(left, up, right, bottom),...]


def translate_crops(crop_tuples, translate_tuple):
    """Translate crop tuples to be over the image are, i.e. left top at (0,0)."""
    crop_tuples_translated = []
    for crop_tuple in crop_tuples:
        crop_tuples_translated.append(
            (
                crop_tuple[0] - translate_tuple[0],
                crop_tuple[1] - translate_tuple[1],
                crop_tuple[2] - translate_tuple[0],
                crop_tuple[3] - translate_tuple[1],
            )
        )
    return crop_tuples_translated


def compute_working_canvas(crop_tuples, bezels=None):
    """Computes effective size of the desktop are taking into account PPI/offsets/bezels.

    When ``bezels`` is provided (a list of ``(right, bottom)`` ppi-normalized
    bezel sizes parallel to ``crop_tuples``), the outer bezels extend the
    canvas so that the rendered image matches what the GUI preview shows. The
    preview sizes its canvas with these bezels included, so omitting them here
    made the applied wallpaper ignore outer bezels.
    """
    # Take the subtractions of right-most right - left-most left
    # and bottom-most bottom - top-most top (=0).
    leftmost = 0
    topmost = 0
    if bezels:
        # Right-/bottom-most edge including each display's outer bezel.
        rightmost = max(round(crp[2] + bez[0]) for crp, bez in zip(crop_tuples, bezels))
        bottommost = max(round(crp[3] + bez[1]) for crp, bez in zip(crop_tuples, bezels))
    else:
        # Right-most edge of the crop tuples.
        rightmost = max(crop_tuples, key=itemgetter(2))[2]
        # Bottom-most edge of the crop tuples.
        bottommost = max(crop_tuples, key=itemgetter(3))[3]
    canvas_size = [rightmost - leftmost, bottommost - topmost]
    return canvas_size
