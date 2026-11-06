# -*- coding: utf-8 -*-
"""macOS : icône 🎙 dans la barre des menus (rumps). Ne contient que l'affichage :
les textes viennent de App.resume_barre() et les actions appellent l'App."""
import rumps

_APP = None


@rumps.notifications
def clic_notification(_info):
    """Clic sur une notification de MiroRecap : ouvre la fenêtre."""
    if _APP:
        _APP.ouvrir_fenetre()


class BarreMenus(rumps.App):
    def __init__(self, app, version, ouvrir_sortie, ouvrir_import):
        global _APP
        _APP = app
        super().__init__("MiroRecap", title="🎙", quit_button=None)
        self.app = app
        self.bouton = rumps.MenuItem("Démarrer l'enregistrement", callback=lambda _: app.basculer_enregistrement())
        self.item_dernier = rumps.MenuItem("Dernier compte rendu : —", callback=lambda _: app.ouvrir_dernier())
        self.item_maj = rumps.MenuItem(f"Version {version} · Rechercher une mise à jour", callback=lambda _: app.clic_maj())
        self.menu = [
            self.bouton,
            rumps.MenuItem("Ouvrir MiroRecap", callback=lambda _: app.ouvrir_fenetre()),
            None,
            rumps.MenuItem("Ouvrir les comptes rendus dans le Finder", callback=lambda _: ouvrir_sortie()),
            rumps.MenuItem("Ouvrir le dossier d'import", callback=lambda _: ouvrir_import()),
            None,
            self.item_dernier,
            None,
            self.item_maj,
            rumps.MenuItem("Quitter MiroRecap", callback=lambda _: app.quitter()),
        ]
        # L'UI de la barre de menus ne doit être modifiée que depuis le thread principal
        self.timer = rumps.Timer(self.tic, 1)
        self.timer.start()

    def tic(self, _):
        self.app.tic()
        r = self.app.resume_barre()
        self.title = r["titre"]
        self.bouton.title = r["bouton"]
        if r["dernier"]:
            self.item_dernier.title = r["dernier"]
        if r["maj"]:
            self.item_maj.title = r["maj"]

    def quitter(self):
        rumps.quit_application()

    def quitter_depuis_un_fil(self):
        """Arrêt demandé hors du fil principal (API de la fenêtre)."""
        from PyObjCTools import AppHelper
        AppHelper.callAfter(rumps.quit_application)
