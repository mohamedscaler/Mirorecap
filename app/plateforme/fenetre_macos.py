# -*- coding: utf-8 -*-
"""macOS : réglages Cocoa de la fenêtre pywebview (identité, Cmd+Q, barre de titre, verre…)."""
import os
import threading
import time


def identite(icone):
    """Remplace l'icône et le nom « Python » par ceux de MiroRecap (Dock, Cmd+Tab, barre de menus)."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle, NSProcessInfo
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "MiroRecap"
        NSProcessInfo.processInfo().setProcessName_("MiroRecap")
        if icone.exists():
            image = NSImage.alloc().initWithContentsOfFile_(str(icone))
            NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        pass


def surveiller_app():
    """Ferme la fenêtre si l'app principale s'arrête (mise à jour, Quitter) :
    sinon elle resterait affichée, figée sur son dernier état."""
    parent = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != parent:   # parent disparu : le système nous a rattachés à launchd
            os._exit(0)


def intercepter_cmd_q(quitter):
    """Cmd+Q (menu Quitter) ferme toute l'app ; le bouton rouge ne ferme que la fenêtre."""
    try:
        import objc
        from webview.platforms.cocoa import BrowserView

        def applicationShouldTerminate_(self, app):
            quitter()
            return 1   # NSTerminateNow

        objc.classAddMethods(BrowserView.AppDelegate, [
            # Signature « I@:@ » : c'est celle que pywebview déclare déjà (« Q@:@ » était refusé,
            # donc Cmd+Q ne quittait pas l'app et elle restait ouverte dans la barre des menus)
            objc.selector(applicationShouldTerminate_, selector=b"applicationShouldTerminate:", signature=b"I@:@")])
    except Exception as e:
        print(f"Cmd+Q : {e}", flush=True)


def accepter_premier_clic():
    """Sur macOS, un clic sur une fenêtre pas encore au premier plan sert d'abord à
    l'activer : il fallait cliquer deux fois sur les boutons. La vue web accepte
    désormais ce premier clic, comme les apps d'Apple."""
    try:
        import objc
        from webview.platforms.cocoa import BrowserView

        def acceptsFirstMouse_(self, evenement):
            return True

        objc.classAddMethods(BrowserView.WebKitHost, [
            objc.selector(acceptsFirstMouse_, selector=b"acceptsFirstMouse:", signature=b"Z@:@")])
    except Exception as e:
        print(f"Premier clic : {e}", flush=True)


def recharger_si_page_vide():
    """Fenêtre vide (seul le fond translucide reste) : macOS a arrêté le processus qui affiche
    la page (manque de mémoire, mise en veille…) ou le chargement a échoué. On recharge la page."""
    try:
        import objc
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView

        def webViewWebContentProcessDidTerminate_(self, webview):
            print("Page arrêtée par macOS : rechargement", flush=True)
            AppHelper.callLater(0.3, webview.reload)

        def webView_didFailProvisionalNavigation_withError_(self, webview, navigation, erreur):
            print(f"Chargement raté ({erreur}) : nouvel essai", flush=True)
            AppHelper.callLater(2.0, webview.reload)

        def webView_didFailNavigation_withError_(self, webview, navigation, erreur):
            AppHelper.callLater(2.0, webview.reload)

        objc.classAddMethods(BrowserView.BrowserDelegate, [
            objc.selector(webViewWebContentProcessDidTerminate_, selector=b"webViewWebContentProcessDidTerminate:", signature=b"v@:@"),
            objc.selector(webView_didFailProvisionalNavigation_withError_, selector=b"webView:didFailProvisionalNavigation:withError:", signature=b"v@:@@@"),
            objc.selector(webView_didFailNavigation_withError_, selector=b"webView:didFailNavigation:withError:", signature=b"v@:@@@")])
    except Exception as e:
        print(f"Rechargement de la page : {e}", flush=True)


def barre_de_titre_integree():
    """Style macOS 26 : le contenu passe sous la barre de titre (transparente), les feux
    tricolores se posent dans la barre latérale. La bande du haut reste déplaçable."""
    try:
        from AppKit import NSApplication, NSColor
        for f in NSApplication.sharedApplication().windows():
            f.setStyleMask_(f.styleMask() | (1 << 15))   # NSWindowStyleMaskFullSizeContentView
            f.setTitlebarAppearsTransparent_(True)
            f.setTitleVisibility_(1)                      # NSWindowTitleHidden
            try:
                # pywebview colore la barre de titre : on la rend transparente
                f.contentView().superview().subviews().lastObject().setBackgroundColor_(NSColor.clearColor())
            except Exception:
                pass
    except Exception as e:
        print(f"Barre de titre : {e}", flush=True)


def mettre_au_premier_plan():
    try:
        from AppKit import NSApplication, NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        NSRunningApplication.currentApplication().activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        app = NSApplication.sharedApplication()
        app.activateIgnoringOtherApps_(True)
        for f in app.windows():
            f.makeKeyAndOrderFront_(None)
    except Exception:
        pass


def couleur_fond():
    """Liquid Glass : la fenêtre laisse voir le bureau flouté derrière (vibrancy macOS),
    l'interface pose ses panneaux de verre par-dessus."""
    try:
        from AppKit import NSApplication
        sombre = "Dark" in str(NSApplication.sharedApplication().effectiveAppearance().name())
        return "#1C1C1F" if sombre else "#ECECF1"
    except Exception:
        return "#ECECF1"


OPTIONS_FENETRE = {"vibrancy": True}   # retirées si pywebview est trop ancien


def avant_creation(icone, quitter):
    identite(icone)
    intercepter_cmd_q(quitter)
    accepter_premier_clic()
    recharger_si_page_vide()


def apres_demarrage(icone):
    # pywebview peut réinitialiser l'icône au lancement : on la réapplique sur le fil principal
    try:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(identite, icone)
        AppHelper.callAfter(barre_de_titre_integree)
        AppHelper.callLater(0.4, mettre_au_premier_plan)   # une fois la fenêtre affichée
    except Exception:
        pass
    threading.Thread(target=surveiller_app, daemon=True).start()


class Api:
    """Appelée par l'interface : la vue web recouvre la barre de titre, donc la bande du haut
    ne déplaçait plus la fenêtre. Un clic dans cette bande demande ici le déplacement natif."""

    def deplacer(self):
        try:
            from AppKit import NSApplication
            from PyObjCTools import AppHelper

            def faire():
                app = NSApplication.sharedApplication()
                evenement = app.currentEvent()
                fen = app.keyWindow() or (app.windows()[0] if app.windows() else None)
                if evenement is not None and fen is not None:
                    fen.performWindowDragWithEvent_(evenement)
            AppHelper.callAfter(faire)
        except Exception as e:
            print(f"Déplacement : {e}", flush=True)

    def agrandir(self):
        """Double-clic dans la bande du haut : agrandir / restaurer, comme toute fenêtre macOS."""
        try:
            from AppKit import NSApplication
            from PyObjCTools import AppHelper

            def faire():
                app = NSApplication.sharedApplication()
                fen = app.keyWindow() or (app.windows()[0] if app.windows() else None)
                if fen is not None:
                    fen.zoom_(None)
            AppHelper.callAfter(faire)
        except Exception as e:
            print(f"Agrandir : {e}", flush=True)
