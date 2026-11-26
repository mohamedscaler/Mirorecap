# -*- coding: utf-8 -*-
"""Windows : icône dans la zone de notification (pystray). Ne contient que l'affichage :
les textes viennent de App.resume_barre() et les actions appellent l'App.
Clic sur l'icône : ouvre la fenêtre ; clic droit : le menu."""
import threading
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from . import systeme_windows

ICONE = Path(__file__).resolve().parent.parent / "icone.png"


def _images():
    """Icône normale et icône « enregistrement en cours » (pastille rouge)."""
    try:
        base = Image.open(ICONE).convert("RGBA").resize((64, 64), Image.LANCZOS)
    except Exception:
        base = Image.new("RGBA", (64, 64), (40, 40, 48, 255))
    rouge = base.copy()
    dessin = ImageDraw.Draw(rouge)
    dessin.ellipse((36, 36, 63, 63), fill=(230, 40, 40, 255), outline=(255, 255, 255, 255), width=3)
    return base, rouge


class BarreMenus:
    def __init__(self, app, version, ouvrir_sortie, ouvrir_import):
        self.app = app
        self.image, self.image_rouge = _images()
        self.textes = {"bouton": "Démarrer l'enregistrement", "dernier": "Dernier compte rendu : —",
                       "maj": f"Version {version} · Rechercher une mise à jour"}
        self._arret = threading.Event()
        texte = lambda cle: (lambda _element: self.textes[cle])   # noqa: E731 (texte relu à chaque ouverture du menu)
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir MiroRecap", lambda: app.ouvrir_fenetre(), default=True),
            pystray.MenuItem(texte("bouton"), lambda: app.basculer_enregistrement()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Ouvrir les comptes rendus dans l'Explorateur", lambda: ouvrir_sortie()),
            pystray.MenuItem("Ouvrir le dossier d'import", lambda: ouvrir_import()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(texte("dernier"), lambda: app.ouvrir_dernier()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(texte("maj"), lambda: app.clic_maj()),
            pystray.MenuItem("Quitter MiroRecap", lambda: app.quitter()),
        )
        self.icone = pystray.Icon("MiroRecap", self.image, "MiroRecap", menu)
        systeme_windows.brancher_icone(self.icone)

    def _boucle(self):
        """Toutes les secondes : travail périodique de l'app et textes à jour."""
        enregistre = False
        while not self._arret.wait(1):
            try:
                self.app.tic()
                r = self.app.resume_barre()
                self.textes["bouton"] = r["bouton"]
                if r["dernier"]:
                    self.textes["dernier"] = r["dernier"]
                if r["maj"]:
                    self.textes["maj"] = r["maj"]
                titre = str(r["titre"]).replace("🎙", "").strip()
                self.icone.title = ("MiroRecap · " + titre if titre else "MiroRecap")[:127]
                if self.app.enreg.actif != enregistre:
                    enregistre = self.app.enreg.actif
                    self.icone.icon = self.image_rouge if enregistre else self.image
                self.icone.update_menu()
            except Exception as e:
                print(f"Zone de notification : {e}", flush=True)

    def run(self):
        def preparer(icone):
            icone.visible = True
            threading.Thread(target=self._boucle, daemon=True).start()
        self.icone.run(setup=preparer)

    def quitter(self):
        self._arret.set()
        self.icone.stop()

    def quitter_depuis_un_fil(self):
        """Arrêt demandé hors du fil principal (API de la fenêtre) : pystray le permet."""
        self.quitter()
