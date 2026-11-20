"""Template registry for ninja-viz.
"""

from . import main as template_main
from . import new_viz as template_new_viz
from . import mainframe as template_mainframe
from . import vertical_drop as template_vertical_drop

TEMPLATES = {
    "main": template_main,
    "new-viz": template_new_viz,
    "mainframe": template_mainframe,
    "vertical-drop": template_vertical_drop,
}

def get_template(name: str):
    if name not in TEMPLATES:
        raise ValueError(f"Unknown template '{name}'. Available templates: {list(TEMPLATES.keys())}")
    return TEMPLATES[name]
