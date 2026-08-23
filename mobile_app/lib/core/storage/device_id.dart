import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:uuid/uuid.dart';

/// A stable per-install identifier, used everywhere the backend keys state by
/// `device_id` (device registration, playback sync, downloads). Generated
/// once and kept in secure storage so it survives app restarts but not
/// reinstalls -- which is the correct behaviour: a reinstalled app is treated
/// as a new device, same as the spec's downloads-per-device model.
class DeviceIdProvider {
  DeviceIdProvider._();

  static const _storage = FlutterSecureStorage();
  static const _key = 'gaanapy_device_id';
  static String? _cached;

  static Future<String> get() async {
    if (_cached != null) return _cached!;
    var id = await _storage.read(key: _key);
    if (id == null) {
      id = const Uuid().v4();
      await _storage.write(key: _key, value: id);
    }
    _cached = id;
    return id;
  }
}
