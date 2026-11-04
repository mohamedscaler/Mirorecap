/*
 * Lanceur natif de MiroRecap.
 *
 * Pourquoi un binaire et pas un script : macOS attribue l'autorisation micro
 * (et iCloud Drive) à l'app qui lance le processus. Ce lanceur porte l'identité
 * "MiroRecap" (Info.plist + description de l'usage du micro), et Python,
 * lancé comme processus enfant, hérite de cette autorisation.
 */
#include <fcntl.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>

extern char **environ;

int main(void) {
    const char *home = getenv("HOME");
    if (!home) return 1;

    char dossier[1024], python[1200], script[1200], log[1200], path[4096];
    snprintf(dossier, sizeof dossier, "%s/Library/Application Support/MiroRecap", home);
    snprintf(python, sizeof python, "%s/.venv/bin/python", dossier);
    snprintf(script, sizeof script, "%s/app.py", dossier);
    snprintf(log, sizeof log, "%s/app.log", dossier);

    /* Une app lancée par macOS a un PATH minimal : on ajoute Homebrew (ffmpeg) */
    const char *ancien = getenv("PATH");
    snprintf(path, sizeof path, "/opt/homebrew/bin:/usr/local/bin:%s", ancien ? ancien : "/usr/bin:/bin");
    setenv("PATH", path, 1);

    /* stdout + stderr de Python → app.log */
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
