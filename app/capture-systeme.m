/*
 * capture-systeme : enregistre le son qui sort du Mac (voix des interlocuteurs en
 * visio : Meet, Zoom, Teams, FaceTime, WhatsApp…) dans un fichier WAV mono 16 bits.
 *
 * Usage : capture-systeme sortie.wav
 * - Lancé par app.py en même temps que l'enregistrement du micro.
 * - S'arrête proprement sur SIGTERM (le fichier WAV est finalisé).
 * - Code de sortie 2 : capture impossible (pas de réponse ou refus de macOS).
 *
 * Utilise un « tap » Core Audio (macOS 14.2+), comme les apps de prise de notes de réunion :
 * macOS demande seulement « Enregistrement audio du système », jamais l'enregistrement de
 * l'écran. Cet outil est livré dans MiroRecap.app (signature stable) : macOS attribue la
 * demande à MiroRecap et s'en souvient d'une version à l'autre.
 */
#import <AudioToolbox/AudioToolbox.h>
#import <CoreAudio/AudioHardwareTapping.h>
#import <CoreAudio/CATapDescription.h>
#import <CoreAudio/CoreAudio.h>
#import <Foundation/Foundation.h>
#include <math.h>
#include <signal.h>

static AudioObjectID tap = kAudioObjectUnknown;
static AudioObjectID agregat = kAudioObjectUnknown;
static AudioDeviceIOProcID procedure = NULL;
static ExtAudioFileRef fichier = NULL;
static AudioStreamBasicDescription format;
static long long echantillons = 0, blocs = 0;
static float niveau = 0;
static int erreurs = 0;

// Libère tout ce qui a été créé auprès de Core Audio (sinon le tap reste actif)
static void liberer(void) {
    if (agregat != kAudioObjectUnknown && procedure) {
        AudioDeviceStop(agregat, procedure);
        AudioDeviceDestroyIOProcID(agregat, procedure);
        procedure = NULL;
    }
    if (agregat != kAudioObjectUnknown) { AudioHardwareDestroyAggregateDevice(agregat); agregat = kAudioObjectUnknown; }
    if (tap != kAudioObjectUnknown) { AudioHardwareDestroyProcessTap(tap); tap = kAudioObjectUnknown; }
}

static dispatch_queue_t file_audio;   // file où arrivent les blocs audio
static volatile sig_atomic_t arret_demande = 0;
static void demander_arret(int signal_recu) { arret_demande = 1; }

// Arrête la lecture (HORS de la file audio : Core Audio attend la fin du bloc en cours, l'appeler
// depuis cette file la bloquait pour toujours), puis finalise l'en-tête WAV et résume la capture
static void terminer(void) {
    liberer();
    dispatch_sync(file_audio, ^{
        if (fichier) { ExtAudioFileDispose(fichier); fichier = NULL; }
    });
    fprintf(stderr, "bilan : %lld échantillons, niveau max %.4f, %d erreurs\n", echantillons, niveau, erreurs);
}

static void echouer(const char *raison, OSStatus statut) {
    fprintf(stderr, "capture du son système impossible : %s (%d)\n", raison, (int)statut);
    liberer();
    exit(2);
}

static NSString *uid_sortie_par_defaut(void) {
    AudioObjectPropertyAddress adresse = {kAudioHardwarePropertyDefaultSystemOutputDevice,
                                          kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    AudioObjectID sortie = kAudioObjectUnknown;
    UInt32 taille = sizeof(sortie);
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &adresse, 0, NULL, &taille, &sortie) != noErr) return nil;
    adresse.mSelector = kAudioDevicePropertyDeviceUID;
    CFStringRef uid = NULL;
    taille = sizeof(uid);
    if (AudioObjectGetPropertyData(sortie, &adresse, 0, NULL, &taille, &uid) != noErr || !uid) return nil;
    return (__bridge_transfer NSString *)uid;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) { fprintf(stderr, "usage : capture-systeme sortie.wav\n"); return 1; }
        NSURL *url = [NSURL fileURLWithPath:@(argv[1])];

        // Blocs audio sur une file ; signaux d'arrêt sur une autre (voir terminer)
        dispatch_queue_t file = dispatch_queue_create("fr.mirorecap.capture", DISPATCH_QUEUE_SERIAL);
        file_audio = file;
        dispatch_queue_t file_arret = dispatch_queue_create("fr.mirorecap.arret", DISPATCH_QUEUE_SERIAL);
        // Arrêt demandé (SIGTERM de l'app, ou Ctrl+C) : le signal lève un drapeau, vérifié toutes
        // les 100 ms. (Les sources de signal de libdispatch ne se déclenchaient pas : l'outil
        // était tué de force et son fichier jamais finalisé.)
        signal(SIGINT, demander_arret);
        signal(SIGTERM, demander_arret);
        dispatch_source_t veille = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, file_arret);
        dispatch_source_set_timer(veille, DISPATCH_TIME_NOW, 100 * NSEC_PER_MSEC, 20 * NSEC_PER_MSEC);
        dispatch_source_set_event_handler(veille, ^{ if (arret_demande) { terminer(); exit(0); } });
        dispatch_resume(veille);

        // 1. Tap mono sur tout le son du Mac (aucune app exclue : le son de test de MiroRecap compte)
        CATapDescription *description = [[CATapDescription alloc] initMonoGlobalTapButExcludeProcesses:@[]];
        description.name = @"MiroRecap";
        description.privateTap = YES;
        description.muteBehavior = CATapUnmuted;   // l'utilisateur continue d'entendre normalement
        OSStatus s = AudioHardwareCreateProcessTap(description, &tap);
        if (s != noErr) echouer("création du tap refusée", s);

        // 2. Périphérique agrégé privé qui lit ce tap (calé sur la sortie son actuelle)
        NSMutableDictionary *config = [@{
            @kAudioAggregateDeviceNameKey: @"MiroRecap capture",
            @kAudioAggregateDeviceUIDKey: NSUUID.UUID.UUIDString,
            @kAudioAggregateDeviceIsPrivateKey: @YES,
            @kAudioAggregateDeviceIsStackedKey: @NO,
            @kAudioAggregateDeviceTapAutoStartKey: @YES,
            @kAudioAggregateDeviceTapListKey: @[@{@kAudioSubTapUIDKey: description.UUID.UUIDString,
                                                  @kAudioSubTapDriftCompensationKey: @YES}],
        } mutableCopy];
        NSString *sortie = uid_sortie_par_defaut();
        if (sortie) {
            config[@kAudioAggregateDeviceMainSubDeviceKey] = sortie;
            config[@kAudioAggregateDeviceSubDeviceListKey] = @[@{@kAudioSubDeviceUIDKey: sortie}];
        }
        s = AudioHardwareCreateAggregateDevice((__bridge CFDictionaryRef)config, &agregat);
        if (s != noErr) echouer("création du périphérique agrégé", s);

        // 3. Format fourni par le tap, puis fichier WAV 16 bits mono à la même fréquence
        AudioObjectPropertyAddress adresse = {kAudioTapPropertyFormat, kAudioObjectPropertyScopeGlobal,
                                              kAudioObjectPropertyElementMain};
        UInt32 taille = sizeof(format);
        s = AudioObjectGetPropertyData(tap, &adresse, 0, NULL, &taille, &format);
        if (s != noErr || format.mSampleRate <= 0) echouer("format du tap illisible", s);

        AudioStreamBasicDescription wav = {0};
        wav.mSampleRate = format.mSampleRate;
        wav.mFormatID = kAudioFormatLinearPCM;
        wav.mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked;
        wav.mChannelsPerFrame = 1;
        wav.mBitsPerChannel = 16;
        wav.mBytesPerFrame = 2;
        wav.mBytesPerPacket = 2;
        wav.mFramesPerPacket = 1;
        s = ExtAudioFileCreateWithURL((__bridge CFURLRef)url, kAudioFileWAVEType, &wav, NULL,
                                      kAudioFileFlags_EraseFile, &fichier);
        if (s != noErr) echouer("création du fichier", s);
        ExtAudioFileSetProperty(fichier, kExtAudioFileProperty_ClientDataFormat, sizeof(format), &format);

        // 4. Lecture : chaque bloc est écrit (ExtAudioFile convertit float → 16 bits)
        BOOL flottant = (format.mFormatFlags & kAudioFormatFlagIsFloat) != 0;
        UInt32 octets_par_image = format.mBytesPerFrame ? format.mBytesPerFrame : sizeof(float);
        s = AudioDeviceCreateIOProcIDWithBlock(&procedure, agregat, file,
            ^(const AudioTimeStamp *maintenant, const AudioBufferList *entree, const AudioTimeStamp *heure_entree,
              AudioBufferList *sortie_audio, const AudioTimeStamp *heure_sortie) {
                if (!fichier || !entree || entree->mNumberBuffers == 0) return;
                blocs++;
                UInt32 images = entree->mBuffers[0].mDataByteSize / octets_par_image;
                if (flottant) {
                    for (UInt32 b = 0; b < entree->mNumberBuffers; b++) {
                        const float *x = entree->mBuffers[b].mData;
                        UInt32 n = entree->mBuffers[b].mDataByteSize / sizeof(float);
                        for (UInt32 i = 0; i < n; i++) { float v = fabsf(x[i]); if (v > niveau) niveau = v; }
                    }
                }
                if (ExtAudioFileWrite(fichier, images, entree) == noErr) echantillons += images;
                else erreurs++;
            });
        if (s != noErr) echouer("création de la lecture", s);
        s = AudioDeviceStart(agregat, procedure);
        if (s != noErr) echouer("démarrage de la lecture refusé", s);
        printf("capture démarrée (%.0f Hz)\n", format.mSampleRate);
        fflush(stdout);

        // 5. Garde-fou : si macOS n'envoie aucun bloc en 3 s, on s'arrête au lieu d'attendre pour rien
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC), file_arret, ^{
            if (blocs == 0) echouer("aucun bloc audio envoyé par macOS", 0);
        });
        dispatch_main();
    }
}
