"""HookyCreator — atelier de contenu pour le remake (mobs, textures, modèles).

L'interface est une application web locale : ``python -m hookycreator`` puis le
navigateur. Aucun code Nintendo, aucune dépendance obligatoire hors HookyMod.
"""

from .project import Project, ProjectError, open_project

__all__ = ["Project", "ProjectError", "open_project"]
__version__ = "1.0.0"
