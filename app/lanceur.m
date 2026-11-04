/*
 * Lanceur natif de MiroRecap (exécutable principal de MiroRecap.app).
 *
 * - Porte l'identité de l'app (Info.plist, autorisation micro) : Python, lancé
 *   comme processus enfant, hérite de cette autorisation.
 * - Petite app Cocoa sans fenêtre : quand on clique sur MiroRecap dans
 *   Applications, le Launchpad ou Spotlight alors qu'elle tourne déjà, macOS lui
 *   envoie « réouvrir » et elle demande à Python d'afficher la fenêtre (SIGUSR1).
 * - Lancée au démarrage du Mac avec --demarrage : elle reste discrète.
 * - Affiche les notifications au nom de MiroRecap : Python les lui transmet par une
 *   notification distribuée « fr.mirorecap.notifier » ; un clic ouvre la fenêtre.
 *
 * Tout est dans le bundle :
 *   MiroRecap.app/Contents/Resources/python   → Python autonome + modules
 *   MiroRecap.app/Contents/Resources/app      → code de l'app (app.py, ui…)
 */
#import <Cocoa/Cocoa.h>
#import <UserNotifications/UserNotifications.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>

extern char **environ;
static pid_t enfant = 0;

@interface Delegue : NSObject <NSApplicationDelegate, UNUserNotificationCenterDelegate>
@end

@implementation Delegue
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    UNUserNotificationCenter *centre = [UNUserNotificationCenter currentNotificationCenter];
    centre.delegate = self;
    [centre requestAuthorizationWithOptions:(UNAuthorizationOptionAlert | UNAuthorizationOptionSound)
                          completionHandler:^(BOOL accorde, NSError *erreur) {}];
    [[NSDistributedNotificationCenter defaultCenter] addObserver:self
                                                        selector:@selector(afficherNotification:)
                                                            name:@"fr.mirorecap.notifier"
                                                          object:nil
                                              suspensionBehavior:NSNotificationSuspensionBehaviorDeliverImmediately];
}

// Demande de Python : on affiche la notification au nom de l'app
- (void)afficherNotification:(NSNotification *)demande {
    NSDictionary *infos = demande.userInfo;
    UNMutableNotificationContent *contenu = [UNMutableNotificationContent new];
    contenu.title = infos[@"titre"] ?: @"MiroRecap";
    contenu.body = infos[@"message"] ?: @"";
    UNNotificationRequest *requete = [UNNotificationRequest requestWithIdentifier:[NSUUID UUID].UUIDString
                                                                          content:contenu trigger:nil];
    [[UNUserNotificationCenter currentNotificationCenter] addNotificationRequest:requete withCompletionHandler:nil];
}

// Afficher la bannière même quand MiroRecap est au premier plan
- (void)userNotificationCenter:(UNUserNotificationCenter *)centre
       willPresentNotification:(UNNotification *)notification
         withCompletionHandler:(void (^)(UNNotificationPresentationOptions))fin {
    fin(UNNotificationPresentationOptionBanner | UNNotificationPresentationOptionList);
}

// Clic sur une notification : on ouvre la fenêtre
- (void)userNotificationCenter:(UNUserNotificationCenter *)centre
didReceiveNotificationResponse:(UNNotificationResponse *)reponse
         withCompletionHandler:(void (^)(void))fin {
    if (enfant > 0) kill(enfant, SIGUSR1);
    fin();
}

// Clic sur l'icône de l'app alors qu'elle tourne : on demande la fenêtre
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visibles {
    if (enfant > 0) kill(enfant, SIGUSR1);
    return NO;
}
- (void)applicationWillTerminate:(NSNotification *)notification {
    if (enfant > 0) kill(enfant, SIGTERM);
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        /* Chemins dans le bundle */
        NSString *contents = [[[NSBundle mainBundle] bundlePath] stringByAppendingPathComponent:@"Contents"];
        NSString *python = [contents stringByAppendingPathComponent:@"Resources/python/bin/python3"];
        NSString *script = [contents stringByAppendingPathComponent:@"Resources/app/demarrage.py"];

        /* Journal dans ~/Library/Application Support/MiroRecap/app.log */
        NSString *dossier = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/MiroRecap"];
        [[NSFileManager defaultManager] createDirectoryAtPath:dossier withIntermediateDirectories:YES attributes:nil error:nil];
        NSString *log = [dossier stringByAppendingPathComponent:@"app.log"];

        /* Claude Code s'installe dans ~/.local/bin : on l'ajoute au PATH */
        const char *ancien = getenv("PATH");
        NSString *path = [NSString stringWithFormat:@"%@/.local/bin:/opt/homebrew/bin:/usr/local/bin:%s",
                          NSHomeDirectory(), ancien ? ancien : "/usr/bin:/bin:/usr/sbin:/sbin"];
        setenv("PATH", path.UTF8String, 1);
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
        setenv("MIRORECAP_LANCEUR", "1", 1);  /* Python sait que le lanceur gère les notifications */

        /* Ouvert par l'utilisateur (pas au démarrage du Mac) : on affiche la fenêtre */
        BOOL demarrage = NO;
        for (int i = 1; i < argc; i++) if (strcmp(argv[i], "--demarrage") == 0) demarrage = YES;
        if (!demarrage) setenv("MIRORECAP_FENETRE", "1", 1);

        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        posix_spawn_file_actions_addopen(&actions, 1, log.fileSystemRepresentation, O_WRONLY | O_CREAT | O_APPEND, 0644);
        posix_spawn_file_actions_adddup2(&actions, 1, 2);

        char *args[] = { (char *)python.fileSystemRepresentation, (char *)script.fileSystemRepresentation, NULL };
        if (posix_spawn(&enfant, args[0], &actions, NULL, args, environ) != 0) return 1;

        /* Quand Python s'arrête (Quitter, mise à jour…), le lanceur s'arrête aussi */
        dispatch_source_t fin = dispatch_source_create(DISPATCH_SOURCE_TYPE_PROC, enfant, DISPATCH_PROC_EXIT, dispatch_get_main_queue());
        dispatch_source_set_event_handler(fin, ^{
            int statut;
            waitpid(enfant, &statut, 0);
            exit(WIFEXITED(statut) ? WEXITSTATUS(statut) : 1);
        });
        dispatch_resume(fin);

        NSApplication *app = [NSApplication sharedApplication];
        Delegue *delegue = [Delegue new];
        app.delegate = delegue;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
    }
    return 0;
}
