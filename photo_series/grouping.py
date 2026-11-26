"""EXIF-paced scene continuity using rotation/scale-tolerant classical features."""
import base64
import cv2
import numpy as np


def extract_features(gray):
    detector = cv2.ORB_create(nfeatures=850, fastThreshold=12)
    points, descriptors = detector.detectAndCompute(gray, None)
    result = dict(points=[[round(p.pt[0], 2), round(p.pt[1], 2)] for p in points],
                descriptors=base64.b64encode(descriptors.tobytes()).decode() if descriptors is not None else '',
                width=gray.shape[1], height=gray.shape[0])
    sift_points, sift = cv2.SIFT_create(nfeatures=650, contrastThreshold=.02).detectAndCompute(gray, None)
    result['secondary'] = dict(points=[[round(p.pt[0], 2), round(p.pt[1], 2)] for p in sift_points],
        descriptors=base64.b64encode(sift.astype(np.uint8).tobytes()).decode() if sift is not None else '',
        width=gray.shape[1], height=gray.shape[0], dimension=128)
    return result


def compare_scene(a, b):
    color = float(np.minimum(a['histogram'], b['histogram']).sum())
    structure = float(np.clip(np.dot(a['structure'], b['structure'])/256, 0, 1))
    result = dict(same_scene=False, color=round(color, 3), structure=round(structure, 3), inliers=0, coverage=0, support=0)
    # Avoid geometric estimation when fixed-camera structure already agrees strongly.
    if structure >= .84 and color >= .74:
        return result | dict(same_scene=True, method='structure')
    left, right = a.get('features', {}), b.get('features', {})
    if not left.get('descriptors') or not right.get('descriptors'):
        return result | dict(same_scene=structure >= .82 and color >= .74, method='structure')
    geometric = compare_geometry(left, right)
    if not geometric['same_scene'] and left.get('secondary') and right.get('secondary'):
        secondary = compare_geometry(left['secondary'], right['secondary'])
        if secondary['same_scene'] or secondary['inliers'] > geometric['inliers']:
            geometric = secondary
    return result | geometric


def compare_geometry(left, right):
    result = dict(same_scene=False, inliers=0, coverage=0, support=0, method='geometry')
    if not left.get('descriptors') or not right.get('descriptors'):
        return result
    dimension = left.get('dimension', 32)
    x = np.frombuffer(base64.b64decode(left['descriptors']), dtype=np.uint8).reshape(-1, dimension)
    y = np.frombuffer(base64.b64decode(right['descriptors']), dtype=np.uint8).reshape(-1, dimension)
    if min(len(x), len(y)) < 12:
        return result | dict(method='insufficient_features')
    pairs = cv2.BFMatcher(cv2.NORM_L2 if dimension == 128 else cv2.NORM_HAMMING).knnMatch(x, y, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .75*pair[1].distance]
    if len(good) < 12:
        return result | dict(method='geometry', matches=len(good))
    p = np.float32([left['points'][m.queryIdx] for m in good])
    q = np.float32([right['points'][m.trainIdx] for m in good])
    _, mask = cv2.findHomography(p, q, cv2.RANSAC, 4.0, maxIters=1500, confidence=.995)
    if mask is None:
        return result | dict(method='geometry', matches=len(good))
    mask = mask.ravel().astype(bool)
    count = int(mask.sum())
    coverage = min(float(cv2.contourArea(cv2.convexHull(points[mask])))/(data['width']*data['height'])
                   for points, data in ((p, left), (q, right))) if count >= 3 else 0
    support = count/min(len(x), len(y))
    inlier_ratio = count/len(good)
    same = ((count >= 18 and inlier_ratio >= .55 and coverage >= .10 and support >= .045)
            or (count >= 50 and inlier_ratio >= .50 and coverage >= .045 and support >= .10))
    return result | dict(same_scene=same, method='geometry', matches=len(good), inliers=count,
                         coverage=round(coverage, 3), support=round(support, 3), inlier_ratio=round(inlier_ratio, 3))


def group_scenes(images, cfg, check_cancel=None):
    positive = [b['sort_time']-a['sort_time'] for a, b in zip(images, images[1:])
                if a['time_source'] == b['time_source'] == 'exif' and b['sort_time'] > a['sort_time']]
    cadence = float(np.median(positive)) if positive else 2.
    if cfg.get('grouping_mode', 'auto') == 'auto':
        horizon = float(np.clip(float(np.percentile(positive, 90))*3 if positive else 12, 12, 180))
    else:
        horizon = cfg['series_gap_seconds']
    groups, decisions = [], []
    for image in images:
        if check_cancel:
            check_cancel()
        evidence = None
        if groups:
            current = groups[-1]
            gap = image['sort_time']-current[-1]['sort_time']
            if gap <= horizon and image['time_source'] == current[-1]['time_source']:
                # Compare real content, not an aspect-ratio-dependent first-frame pixel grid.
                for candidate in reversed(current[-4:]):
                    if image['sort_time']-candidate['sort_time'] > horizon:
                        continue
                    comparison = compare_scene(candidate, image)
                    if evidence is None or comparison['same_scene']:
                        evidence = comparison | dict(compared_with=candidate['filename'])
                    if comparison['same_scene']:
                        break
            decisions.append(dict(before=current[-1]['filename'], after=image['filename'], gap_seconds=round(gap, 3),
                                  joined=bool(evidence and evidence['same_scene']), evidence=evidence))
        if not groups or not evidence or not evidence['same_scene']:
            groups.append([])
        groups[-1].append(image)
    return groups, dict(mode='auto_scene_exif' if cfg.get('grouping_mode', 'auto') == 'auto' else 'configured',
                        algorithm_version=3, cadence_seconds=cadence, inferred_gap_seconds=horizon, decisions=decisions)
