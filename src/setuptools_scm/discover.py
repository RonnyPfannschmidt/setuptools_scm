import os
from pkg_resources import iter_entry_points
from .utils import trace


def iter_matching_entrypoints(path, entrypoint):
    trace("looking for ", entrypoint=entrypoint, path=path)
    for ep in iter_entry_points(entrypoint):
        if os.path.exists(os.path.join(path, ep.name)):
            if os.path.isabs(ep.name):
                trace("ignoring bad ep", ep, indent=2)
            trace("found ep", ep, indent=2)
            yield ep
