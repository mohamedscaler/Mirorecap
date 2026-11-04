/*
 * Lanceur natif de MiroRecap (exécutable principal de MiroRecap.app).
 *
 * Pourquoi un binaire et pas un script : macOS attribue l'autorisation micro
 * (et iCloud Drive) à l'app qui lance le processus. Ce lanceur porte l'identité
 * "MiroRecap" (Info.plist + description de l'usage du micro), et Python,
 * lancé comme processus enfant, hérite de cette autorisation.
 *
 * Tout est dans le bundle :
 *   MiroRecap.app/Contents/Resources/python   → Python autonome + modules
 *   MiroRecap.app/Contents/Resources/app      → code de l'app (app.py, ui…)
 */
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>

extern char **environ;

int main(void) {
    /* Chemin réel de cet exécutable → dossier Contents du bundle */
    char brut[PATH_MAX], exe[PATH_MAX];
    uint32_t taille = sizeof brut;
    if (_NSGetExecutablePath(brut, &taille) != 0 || !realpath(brut, exe)) return 1;
    char macos[PATH_MAX], contents[PATH_MAX];
    strncpy(macos, dirname(exe), sizeof macos);
    strncpy(contents, dirname(macos), sizeof contents);

    char python[PATH_MAX], script[PATH_MAX];
    snprintf(python, sizeof python, "%s/Resources/python/bin/python3", contents);
    snprintf(script, sizeof script, "%s/Resources/app/app.py", contents);

    /* Journal dans ~/Library/Application Support/MiroRecap/app.log */
    const char *home = getenv("HOME");
    if (!home) return 1;
    char dossier[PATH_MAX], log[PATH_MAX];
    snprintf(dossier, sizeof dossier, "%s/Library/Application Support/MiroRecap", home);
    mkdir(dossier, 0755);
    snprintf(log, sizeof log, "%s/app.log", dossier);

    /* Python n'a pas besoin du PATH de l'utilisateur, mais Claude Code oui */
    const char *ancien = getenv("PATH");
    char path[4096];
    snprintf(path, sizeof path, "%s/.local/bin:/opt/homebrew/bin:/usr/local/bin:%s",
             home, ancien ? ancien : "/usr/bin:/bin:/usr/sbin:/sbin");
    setenv("PATH", path, 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1); /* le bundle reste intact */

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addopen(&actions, 1, log, O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_adddup2(&actions, 1, 2);

    char *argv[] = { python, script, NULL };
    pid_t pid;
    if (posix_spawn(&pid, python, &actions, NULL, argv, environ) != 0) return 1;

    int statut;
    waitpid(pid, &statut, 0);
    return WIFEXITED(statut) ? WEXITSTATUS(statut) : 1;
}
