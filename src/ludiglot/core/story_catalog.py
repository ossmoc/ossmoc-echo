"""Human-readable story hints built only from identities in verified data."""
from dataclasses import dataclass
import re


def dialogue_scene(key):
    # Both versioned Main IDs and unversioned Side IDs end in scene/line.
    match = re.fullmatch(r'((?:Main|Side)_[A-Za-z0-9_]+)_(\d+)_(\d+)', str(key))
    return (match[1], int(match[2]), int(match[3])) if match else None


@dataclass(frozen=True)
class StoryGroup:
    key: str
    label: str
    version: str
    seed: str
    count: int


AREAS = {'Mengzhou':'Mengzhou', 'LahaiRoi':'Lahai-Roi', 'HuangLong':'Huanglong',
         'Huanglong':'Huanglong', 'Rinascita':'Rinascita', 'Linaxita':'Rinascita'}


def story_label(prefix):
    match = re.match(r'Main_([^_]+)_(\d+)_(\d+)(?:_|$)', prefix)
    if match:
        version = f'{int(match[2])}.{int(match[3])}'
        return version + ' · ' + AREAS.get(match[1], 'Cốt truyện chính'), version
    area = re.match(r'Main_([^_]+)', prefix)
    return ('Cốt truyện · ' + AREAS.get(area[1], 'Nhóm khác') if area else 'Nhiệm vụ phụ'), ''


def story_catalog(records):
    groups = {}
    for record in records:
        key = record.identity[1]
        scene = dialogue_scene(key)
        if key.startswith('Main_') and scene:
            groups.setdefault(scene[0], []).append(key)
    result = []
    for prefix, keys in groups.items():
        label, version = story_label(prefix)
        keys = sorted(set(keys), key=lambda key: dialogue_scene(key)[1:])
        result.append(StoryGroup(prefix, label, version, keys[0], len(keys)))
    # Duplicate source labels (e.g. quest subgroups) are distinct visible parts,
    # not invented mission titles and never arbitrary raw IDs.
    totals = {}; positions = {}
    for group in result:
        totals[group.label] = totals.get(group.label, 0)+1
    ordered = sorted(result, key=lambda group: (
        tuple(int(n) for n in group.version.split('.')) if group.version else (999,999), group.label, group.key))
    output = []
    for group in ordered:
        positions[group.label] = positions.get(group.label, 0)+1
        label = group.label + (f' · phần {positions[group.label]}' if totals[group.label]>1 else '')
        output.append(StoryGroup(group.key,label,group.version,group.seed,group.count))
    return tuple(output)
