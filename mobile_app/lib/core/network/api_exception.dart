/// Mirrors the `{success, data, error: {code, message, details}}` envelope
/// every GaanaPy backend response uses (see app/utils/response.py).
class ApiException implements Exception {
  final String code;
  final String message;
  final int? statusCode;
  final dynamic details;

  const ApiException({
    required this.code,
    required this.message,
    this.statusCode,
    this.details,
  });

  bool get isUnauthorized => statusCode == 401;
  bool get isNotFound => statusCode == 404;

  @override
  String toString() => 'ApiException($code, $statusCode): $message';
}
