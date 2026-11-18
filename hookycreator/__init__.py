"""HookyCreator — atelier local pour modèles, textures et autres ressources.

L'interface est une application web locale : ``python -m hookycreator`` puis le
navigateur. Les ressources à traiter sont fournies par l'utilisateur.
"""

from .project import Project, ProjectError, open_project

__all__ = ["Project", "ProjectError", "open_project"]
__version__ = "11.26.1"
