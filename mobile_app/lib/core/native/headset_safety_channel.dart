import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

/// Bridges to HeadsetSafetyService.kt, an Android foreground service that
/// tracks continuous headset/Bluetooth connection time independently of the
/// Flutter UI/Activity lifecycle -- a Dart Timer would be suspended the
/// moment the app leaves the foreground, which defeats the point of a
/// safety reminder.
///
/// Android only: there is no equivalent always-on background capability on
/// iOS for arbitrary audio-route monitoring, so these calls are no-ops there.
class HeadsetSafetyChannel {
  HeadsetSafetyChannel._();

  static const _channel = MethodChannel('com.musichub.app/headset_safety');

  static Future<void> start() async {
    if (!Platform.isAndroid) return;
    final status = await Permission.notification.status;
    if (status.isDenied) {
      final result = await Permission.notification.request();
      if (!result.isGranted) {
        // Proceeding anyway: the service still tracks connection time, it
        // just can't surface the reminder until the user grants the
        // permission (e.g. from system settings later).
        debugPrint('Headset safety: notification permission not granted');
      }
    }
    try {
      await _channel.invokeMethod('start');
    } on PlatformException catch (e) {
      debugPrint('Headset safety: failed to start monitoring: $e');
    }
  }

  static Future<void> stop() async {
    if (!Platform.isAndroid) return;
    try {
      await _channel.invokeMethod('stop');
    } on PlatformException catch (e) {
      debugPrint('Headset safety: failed to stop monitoring: $e');
    }
  }
}
