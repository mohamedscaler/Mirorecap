/*
 * capture-systeme : enregistre le son qui sort du Mac (voix des interlocuteurs en
 * visio : Meet, Zoom, Teams, FaceTime…) dans un fichier WAV mono 16 bits.
 *
 * Usage : capture-systeme sortie.wav
 * - Lancé par app.py en même temps que l'enregistrement du micro.
 * - S'arrête proprement sur SIGTERM (le fichier WAV est finalisé).
 * - Code de sortie 2 : capture refusée (autorisation « Enregistrement de l'écran
 *   et de l'audio système » non accordée) ou indisponible.
 *
 * Utilise ScreenCaptureKit (macOS 13+) en audio seul : aucune image n'est conservée.
 * Lancé depuis MiroRecap.app, l'autorisation demandée est celle de MiroRecap.
 */
#import <AudioToolbox/AudioToolbox.h>
#import <CoreMedia/CoreMedia.h>
#import <Foundation/Foundation.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#include <math.h>
#include <signal.h>

@interface Capteur : NSObject <SCStreamOutput, SCStreamDelegate>
@property(nonatomic) NSURL *url;
@property(nonatomic) SCStream *flux;
@end

@implementation Capteur {
    ExtAudioFileRef _fichier;   // créé au premier bloc audio (on connaît alors le format exact)
    long long _echantillons;    // diagnostic : quantité écrite et niveau maximal
    float _niveau;
    int _erreurs;
}

- (void)stream:(SCStream *)stream didOutputSampleBuffer:(CMSampleBufferRef)tampon ofType:(SCStreamOutputType)type {
    if (type != SCStreamOutputTypeAudio || !CMSampleBufferDataIsReady(tampon)) return;
    const AudioStreamBasicDescription *entree =
        CMAudioFormatDescriptionGetStreamBasicDescription(CMSampleBufferGetFormatDescription(tampon));
    if (!entree) return;

    if (!_fichier) {
        // Fichier : WAV PCM 16 bits mono, à la fréquence fournie par macOS
        AudioStreamBasicDescription sortie = {0};
        sortie.mSampleRate = entree->mSampleRate;
        sortie.mFormatID = kAudioFormatLinearPCM;
        sortie.mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked;
        sortie.mChannelsPerFrame = 1;
        sortie.mBitsPerChannel = 16;
        sortie.mBytesPerFrame = 2;
        sortie.mBytesPerPacket = 2;
        sortie.mFramesPerPacket = 1;
        OSStatus s = ExtAudioFileCreateWithURL((__bridge CFURLRef)self.url, kAudioFileWAVEType, &sortie, NULL,
                                               kAudioFileFlags_EraseFile, &_fichier);
        if (s != noErr) { fprintf(stderr, "création du fichier impossible (%d)\n", (int)s); exit(3); }
        // Format des blocs reçus : ExtAudioFile convertit (float → 16 bits, canaux → mono)
        ExtAudioFileSetProperty(_fichier, kExtAudioFileProperty_ClientDataFormat, sizeof(*entree), entree);
    }

    // Récupère les données audio du bloc. Appel en deux temps (taille, puis contenu) avec
    // EXACTEMENT les mêmes options : sinon la taille ne correspond pas et la lecture échoue.
    const uint32_t options = kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment;
    size_t taille = 0;
    OSStatus s = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        tampon, &taille, NULL, 0, kCFAllocatorDefault, kCFAllocatorDefault, options, NULL);
    if (s != noErr || taille == 0) { _erreurs++; return; }
    AudioBufferList *liste = malloc(taille);
    CMBlockBufferRef bloc = NULL;
    s = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        tampon, NULL, liste, taille, kCFAllocatorDefault, kCFAllocatorDefault, options, &bloc);
    if (s == noErr) {
        UInt32 images = (UInt32)CMSampleBufferGetNumSamples(tampon);
        // Niveau maximal (données en float pour ScreenCaptureKit)
        if (entree->mFormatFlags & kAudioFormatFlagIsFloat) {
            for (UInt32 b = 0; b < liste->mNumberBuffers; b++) {
                float *x = liste->mBuffers[b].mData;
                UInt32 n = liste->mBuffers[b].mDataByteSize / sizeof(float);
                for (UInt32 i = 0; i < n; i++) { float v = fabsf(x[i]); if (v > _niveau) _niveau = v; }
            }
        }
        if (ExtAudioFileWrite(_fichier, images, liste) == noErr) _echantillons += images;
        else _erreurs++;
    } else {
        _erreurs++;
    }
    free(liste);
    if (bloc) CFRelease(bloc);
}

- (void)stream:(SCStream *)stream didStopWithError:(NSError *)erreur {
    fprintf(stderr, "capture interrompue : %s\n", erreur.localizedDescription.UTF8String);
    [self terminer];
    exit(4);
}

// Finalise l'en-tête WAV (sinon le fichier est illisible) et résume la capture
- (void)terminer {
    if (_fichier) { ExtAudioFileDispose(_fichier); _fichier = NULL; }
    fprintf(stderr, "bilan : %lld échantillons, niveau max %.4f, %d erreurs\n", _echantillons, _niveau, _erreurs);
}
@end

static void refuser(NSError *erreur) {
    fprintf(stderr, "capture du son système refusée : %s\n",
            erreur ? erreur.localizedDescription.UTF8String : "aucun écran");
    exit(2);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) { fprintf(stderr, "usage : capture-systeme sortie.wav\n"); return 1; }
        Capteur *capteur = [Capteur new];
        capteur.url = [NSURL fileURLWithPath:@(argv[1])];

        // Toute l'écriture se fait sur cette file : l'arrêt y passe aussi, donc jamais
        // pendant l'écriture d'un bloc
        dispatch_queue_t file = dispatch_queue_create("fr.mirorecap.capture", DISPATCH_QUEUE_SERIAL);
        const int signaux[] = {SIGINT, SIGTERM};
        for (int i = 0; i < 2; i++) {
            int sig = signaux[i];
            signal(sig, SIG_IGN);
            dispatch_source_t source = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, sig, 0, file);
            dispatch_source_set_event_handler(source, ^{
                [capteur terminer];
                exit(0);
            });
            dispatch_resume(source);
        }

        [SCShareableContent getShareableContentWithCompletionHandler:^(SCShareableContent *contenu, NSError *erreur) {
            if (erreur || contenu.displays.count == 0) refuser(erreur);

            // Un filtre « écran entier » est obligatoire, mais on ne garde que l'audio
            SCContentFilter *filtre = [[SCContentFilter alloc] initWithDisplay:contenu.displays.firstObject
                                                              excludingWindows:@[]];
            SCStreamConfiguration *config = [SCStreamConfiguration new];
            config.capturesAudio = YES;
            config.excludesCurrentProcessAudio = YES;
            config.sampleRate = 48000;
            config.channelCount = 1;
            // Image minuscule et 1 image/s : coût négligeable (elle n'est pas lue)
            config.width = 2;
            config.height = 2;
            config.minimumFrameInterval = CMTimeMake(1, 1);

            capteur.flux = [[SCStream alloc] initWithFilter:filtre configuration:config delegate:capteur];
            NSError *e = nil;
            [capteur.flux addStreamOutput:capteur type:SCStreamOutputTypeAudio sampleHandlerQueue:file error:&e];
            if (e) refuser(e);
            [capteur.flux startCaptureWithCompletionHandler:^(NSError *e2) {
                if (e2) refuser(e2);
                printf("capture démarrée\n");
                fflush(stdout);
            }];
        }];
        dispatch_main();
    }
}
