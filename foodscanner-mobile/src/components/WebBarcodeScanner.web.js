import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator, Animated } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { BrowserMultiFormatReader, BarcodeFormat, DecodeHintType } from '@zxing/library';
import { PREMIUM_COLORS, PREMIUM_SHADOWS, PREMIUM_RADIUS } from '../theme/premiumTheme';

export default function WebBarcodeScanner({
  onBarcodeScanned,
  onClose,
  boxSize = 260,
}) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const readerRef = useRef(null);
  const isScanningRef = useRef(false);

  const [cameraState, setCameraState] = useState('initializing'); // 'initializing' | 'active' | 'permission_denied' | 'not_found' | 'error' | 'detected'
  const [errorMessage, setErrorMessage] = useState('');
  const [detectedCode, setDetectedCode] = useState(null);

  // Animated laser line
  const laserAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const loopAnim = Animated.loop(
      Animated.sequence([
        Animated.timing(laserAnim, {
          toValue: boxSize - 20,
          duration: 1800,
          useNativeDriver: true,
        }),
        Animated.timing(laserAnim, {
          toValue: 0,
          duration: 1800,
          useNativeDriver: true,
        }),
      ])
    );
    loopAnim.start();

    return () => {
      loopAnim.stop();
    };
  }, [laserAnim, boxSize]);

  const stopCamera = () => {
    isScanningRef.current = false;
    if (readerRef.current) {
      try {
        readerRef.current.reset();
      } catch (_e) {}
      readerRef.current = null;
    }
    if (streamRef.current) {
      try {
        streamRef.current.getTracks().forEach((track) => {
          track.stop();
        });
      } catch (_e) {}
      streamRef.current = null;
    }
    if (videoRef.current) {
      try {
        videoRef.current.srcObject = null;
      } catch (_e) {}
    }
  };

  const startCamera = async () => {
    stopCamera();
    setCameraState('initializing');
    setErrorMessage('');
    setDetectedCode(null);

    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setCameraState('error');
      setErrorMessage('Webcam access is not supported in this browser. Please use a modern browser such as Chrome, Edge, or Safari.');
      return;
    }

    try {
      const constraints = {
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      };

      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;

      if (!videoRef.current) {
        setCameraState('error');
        setErrorMessage('Video display element not ready. Please try again.');
        return;
      }

      videoRef.current.srcObject = stream;
      videoRef.current.setAttribute('playsinline', 'true');
      await videoRef.current.play();

      setCameraState('active');
      isScanningRef.current = true;

      initBarcodeDetection(videoRef.current);
    } catch (err) {
      const name = err?.name || '';
      if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        setCameraState('permission_denied');
        setErrorMessage('Camera access was denied. Please allow camera permissions in your browser address bar and click "Try Again".');
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        setCameraState('not_found');
        setErrorMessage('No camera was detected on this device. Please connect a webcam or enter the barcode manually.');
      } else if (name === 'NotReadableError' || name === 'TrackStartError') {
        setCameraState('error');
        setErrorMessage('Webcam is currently in use by another application. Please close other camera apps and retry.');
      } else {
        setCameraState('error');
        setErrorMessage(err?.message || 'Could not access laptop webcam. Please check browser permissions.');
      }
    }
  };

  const handleBarcodeFound = (code) => {
    if (!isScanningRef.current) return;
    const cleanCode = String(code || '').trim();
    if (!cleanCode) return;

    // Immediately stop scanning loop to prevent duplicate calls
    isScanningRef.current = false;
    setDetectedCode(cleanCode);
    setCameraState('detected');

    if (onBarcodeScanned) {
      onBarcodeScanned({ data: cleanCode });
    }
  };

  const initBarcodeDetection = (videoElement) => {
    // Priority 1: Native BarcodeDetector API (fast hardware acceleration in modern Chromium)
    if (typeof window !== 'undefined' && 'BarcodeDetector' in window) {
      try {
        const formats = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'qr_code'];
        const detector = new window.BarcodeDetector({ formats });

        const scanFrame = async () => {
          if (!isScanningRef.current || !videoElement) return;
          try {
            if (videoElement.readyState >= 2) {
              const barcodes = await detector.detect(videoElement);
              if (barcodes && barcodes.length > 0) {
                const raw = barcodes[0].rawValue;
                if (raw) {
                  handleBarcodeFound(raw);
                  return;
                }
              }
            }
          } catch (_e) {
            // Ignore individual frame miss
          }
          if (isScanningRef.current) {
            setTimeout(scanFrame, 120);
          }
        };

        scanFrame();
        return;
      } catch (detectorErr) {
        console.warn('Native BarcodeDetector fallback to ZXing reader:', detectorErr);
      }
    }

    // Priority 2: @zxing/library fallback (universal browser support)
    try {
      const hints = new Map();
      hints.set(DecodeHintType.POSSIBLE_FORMATS, [
        BarcodeFormat.EAN_13,
        BarcodeFormat.EAN_8,
        BarcodeFormat.UPC_A,
        BarcodeFormat.UPC_E,
        BarcodeFormat.CODE_128,
        BarcodeFormat.CODE_39,
        BarcodeFormat.QR_CODE,
      ]);
      hints.set(DecodeHintType.TRY_HARDER, true);

      const reader = new BrowserMultiFormatReader(hints, 200);
      readerRef.current = reader;

      reader.decodeFromVideoElement(videoElement, (result, err) => {
        if (!isScanningRef.current) return;
        if (result) {
          const text = result.getText();
          if (text) {
            handleBarcodeFound(text);
          }
        }
      });
    } catch (zxingErr) {
      console.error('ZXing reader error:', zxingErr);
    }
  };

  useEffect(() => {
    startCamera();
    return () => {
      stopCamera();
    };
  }, []);

  return (
    <View style={styles.container}>
      {/* Video Element rendered for Web DOM */}
      <video
        ref={videoRef}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          position: 'absolute',
          top: 0,
          left: 0,
          display: cameraState === 'active' || cameraState === 'detected' ? 'block' : 'none',
        }}
        autoPlay
        playsInline
        muted
      />

      {/* State: Initializing */}
      {cameraState === 'initializing' && (
        <View style={styles.centerState}>
          <ActivityIndicator size="large" color={PREMIUM_COLORS.primaryDark} />
          <Text style={styles.stateTitle}>Starting Laptop Webcam...</Text>
          <Text style={styles.stateSub}>Please allow camera permissions if prompted by your browser</Text>
        </View>
      )}

      {/* State: Permission Denied */}
      {cameraState === 'permission_denied' && (
        <View style={styles.centerState}>
          <View style={styles.errorIconWrap}>
            <Ionicons name="videocam-off-outline" size={34} color={PREMIUM_COLORS.status.avoid} />
          </View>
          <Text style={styles.errorTitle}>Camera Permission Blocked</Text>
          <Text style={styles.errorDescription}>{errorMessage}</Text>
          <View style={styles.buttonRow}>
            <TouchableOpacity style={styles.retryBtn} onPress={startCamera} activeOpacity={0.85}>
              <Ionicons name="refresh-outline" size={16} color={PREMIUM_COLORS.white} />
              <Text style={styles.retryBtnText}>Try Again</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.cancelBtn} onPress={onClose} activeOpacity={0.85}>
              <Text style={styles.cancelBtnText}>Enter Manually</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* State: Camera Error / Unavailable */}
      {(cameraState === 'error' || cameraState === 'not_found') && (
        <View style={styles.centerState}>
          <View style={styles.errorIconWrap}>
            <Ionicons name="alert-circle-outline" size={34} color={PREMIUM_COLORS.status.avoid} />
          </View>
          <Text style={styles.errorTitle}>Webcam Unavailable</Text>
          <Text style={styles.errorDescription}>{errorMessage}</Text>
          <View style={styles.buttonRow}>
            <TouchableOpacity style={styles.retryBtn} onPress={startCamera} activeOpacity={0.85}>
              <Ionicons name="refresh-outline" size={16} color={PREMIUM_COLORS.white} />
              <Text style={styles.retryBtnText}>Retry</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.cancelBtn} onPress={onClose} activeOpacity={0.85}>
              <Text style={styles.cancelBtnText}>Manual Search</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* State: Active or Detected Overlay */}
      {(cameraState === 'active' || cameraState === 'detected') && (
        <View style={styles.reticleOverlay} pointerEvents="box-none">
          <View style={[styles.reticleBox, { width: boxSize, height: boxSize }]}>
            <View style={[styles.corner, styles.cornerTL]} />
            <View style={[styles.corner, styles.cornerTR]} />
            <View style={[styles.corner, styles.cornerBL]} />
            <View style={[styles.corner, styles.cornerBR]} />

            {cameraState === 'active' && (
              <Animated.View
                style={[
                  styles.laserLine,
                  { transform: [{ translateY: laserAnim }] },
                ]}
              />
            )}

            {cameraState === 'detected' && (
              <View style={styles.detectedBadge}>
                <Ionicons name="checkmark-circle" size={28} color={PREMIUM_COLORS.white} />
                <Text style={styles.detectedTitle}>Barcode Detected</Text>
                <Text style={styles.detectedCode}>{detectedCode}</Text>
              </View>
            )}
          </View>

          <Text style={styles.cameraHint}>
            {cameraState === 'detected'
              ? 'Fetching scientific product analysis...'
              : 'Hold packaging barcode steady in front of your webcam'}
          </Text>

          <TouchableOpacity
            style={[styles.closeCameraBtn, PREMIUM_SHADOWS.sm]}
            activeOpacity={0.85}
            onPress={() => {
              stopCamera();
              if (onClose) onClose();
            }}
          >
            <Text style={styles.closeCameraText}>✕ Close Webcam</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: '100%',
    height: '100%',
    backgroundColor: '#0A0E0A',
    borderRadius: PREMIUM_RADIUS.xl,
    overflow: 'hidden',
    position: 'relative',
    justifyContent: 'center',
    alignItems: 'center',
  },
  centerState: {
    padding: 24,
    alignItems: 'center',
    justifyContent: 'center',
    maxWidth: 360,
  },
  stateTitle: {
    marginTop: 16,
    fontSize: 16,
    fontWeight: '700',
    color: PREMIUM_COLORS.white,
    textAlign: 'center',
  },
  stateSub: {
    marginTop: 6,
    fontSize: 13,
    color: '#9CA3AF',
    textAlign: 'center',
    lineHeight: 18,
  },
  errorIconWrap: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: 'rgba(239, 68, 68, 0.15)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  errorTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: PREMIUM_COLORS.white,
    textAlign: 'center',
    marginBottom: 6,
  },
  errorDescription: {
    fontSize: 13,
    color: '#D1D5DB',
    textAlign: 'center',
    lineHeight: 18,
    marginBottom: 16,
  },
  buttonRow: {
    flexDirection: 'row',
    gap: 12,
  },
  retryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: PREMIUM_COLORS.primary,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: PREMIUM_RADIUS.md,
  },
  retryBtnText: {
    color: PREMIUM_COLORS.white,
    fontWeight: '700',
    fontSize: 13,
  },
  cancelBtn: {
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: PREMIUM_RADIUS.md,
    justifyContent: 'center',
  },
  cancelBtnText: {
    color: PREMIUM_COLORS.white,
    fontWeight: '600',
    fontSize: 13,
  },
  reticleOverlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.25)',
  },
  reticleBox: {
    position: 'relative',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.25)',
    borderRadius: 14,
    overflow: 'hidden',
    backgroundColor: 'transparent',
  },
  corner: {
    position: 'absolute',
    width: 22,
    height: 22,
    borderColor: '#4ADE80',
  },
  cornerTL: {
    top: 0,
    left: 0,
    borderTopWidth: 4,
    borderLeftWidth: 4,
    borderTopLeftRadius: 10,
  },
  cornerTR: {
    top: 0,
    right: 0,
    borderTopWidth: 4,
    borderRightWidth: 4,
    borderTopRightRadius: 10,
  },
  cornerBL: {
    bottom: 0,
    left: 0,
    borderBottomWidth: 4,
    borderLeftWidth: 4,
    borderBottomLeftRadius: 10,
  },
  cornerBR: {
    bottom: 0,
    right: 0,
    borderBottomWidth: 4,
    borderRightWidth: 4,
    borderBottomRightRadius: 10,
  },
  laserLine: {
    position: 'absolute',
    left: 0,
    right: 0,
    height: 2,
    backgroundColor: '#4ADE80',
    shadowColor: '#4ADE80',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.9,
    shadowRadius: 6,
  },
  cameraHint: {
    color: PREMIUM_COLORS.white,
    fontSize: 13,
    fontWeight: '600',
    marginTop: 18,
    textAlign: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.65)',
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: PREMIUM_RADIUS.pill,
  },
  closeCameraBtn: {
    marginTop: 14,
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: 'rgba(239, 68, 68, 0.85)',
    borderRadius: PREMIUM_RADIUS.pill,
  },
  closeCameraText: {
    color: PREMIUM_COLORS.white,
    fontSize: 12,
    fontWeight: '700',
  },
  detectedBadge: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(34, 197, 94, 0.85)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 12,
  },
  detectedTitle: {
    color: PREMIUM_COLORS.white,
    fontSize: 14,
    fontWeight: '800',
    marginTop: 6,
  },
  detectedCode: {
    color: PREMIUM_COLORS.white,
    fontSize: 16,
    fontWeight: '900',
    letterSpacing: 1,
    marginTop: 2,
  },
});
