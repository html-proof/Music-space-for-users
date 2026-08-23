import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';

import '../config/app_config.dart';
import 'api_exception.dart';

/// Thin wrapper around Dio that:
///  - points at the FastAPI backend
///  - attaches the current Firebase ID token as a Bearer header on every
///    request (never a raw uid -- the backend re-derives identity from the
///    token itself, see app/middleware/firebase_auth.py)
///  - unwraps the `{success, data, error}` envelope every endpoint returns
///  - surfaces backend errors as [ApiException] instead of raw DioException
class ApiClient {
  ApiClient({void Function()? onUnauthorized})
      : _onUnauthorized = onUnauthorized {
    _dio = Dio(
      BaseOptions(
        baseUrl: AppConfig.apiBaseUrl,
        // Generous on purpose: the deployed backend is a Render free-tier
        // instance, which spins down on inactivity and cold-starts on the
        // next request -- confirmed to take longer than the previous 15s
        // connectTimeout, which is exactly what surfaced as a sign-in
        // failure after an idle period. 45s comfortably covers a cold start;
        // a warm instance still responds in a couple of seconds either way.
        connectTimeout: const Duration(seconds: 45),
        receiveTimeout: const Duration(seconds: 45),
        headers: {'Content-Type': 'application/json'},
      ),
    );
    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) async {
          final user = FirebaseAuth.instance.currentUser;
          if (user != null) {
            final token = await user.getIdToken();
            options.headers['Authorization'] = 'Bearer $token';
          }
          handler.next(options);
        },
        onError: (error, handler) {
          if (error.response?.statusCode == 401) {
            _onUnauthorized?.call();
          }
          handler.next(error);
        },
      ),
    );
  }

  late final Dio _dio;
  final void Function()? _onUnauthorized;

  Dio get raw => _dio;

  Future<dynamic> get(String path, {Map<String, dynamic>? query}) =>
      _unwrap(() => _dio.get(path, queryParameters: _clean(query)));

  Future<dynamic> post(String path, {dynamic body, Map<String, dynamic>? query}) =>
      _unwrap(() => _dio.post(path, data: body, queryParameters: _clean(query)));

  Future<dynamic> patch(String path, {dynamic body}) =>
      _unwrap(() => _dio.patch(path, data: body));

  Future<dynamic> put(String path, {dynamic body}) =>
      _unwrap(() => _dio.put(path, data: body));

  Future<dynamic> delete(String path, {Map<String, dynamic>? query}) =>
      _unwrap(() => _dio.delete(path, queryParameters: _clean(query)));

  Map<String, dynamic>? _clean(Map<String, dynamic>? query) {
    if (query == null) return null;
    final cleaned = Map<String, dynamic>.from(query)
      ..removeWhere((_, v) => v == null);
    return cleaned.isEmpty ? null : cleaned;
  }

  Future<dynamic> _unwrap(Future<Response> Function() call) async {
    try {
      final response = await call();
      final body = response.data;
      if (body is Map<String, dynamic> && body.containsKey('success')) {
        if (body['success'] == true) {
          return body['data'];
        }
        final error = body['error'] as Map<String, dynamic>?;
        throw ApiException(
          code: error?['code']?.toString() ?? 'UNKNOWN_ERROR',
          message: error?['message']?.toString() ?? 'Something went wrong',
          statusCode: response.statusCode,
          details: error?['details'],
        );
      }
      return body;
    } on DioException catch (e) {
      final body = e.response?.data;
      if (body is Map<String, dynamic> && body['error'] is Map) {
        final error = body['error'] as Map<String, dynamic>;
        throw ApiException(
          code: error['code']?.toString() ?? 'REQUEST_FAILED',
          message: error['message']?.toString() ?? e.message ?? 'Request failed',
          statusCode: e.response?.statusCode,
          details: error['details'],
        );
      }
      throw ApiException(
        code: 'NETWORK_ERROR',
        message: e.message ?? 'Could not reach the server',
        statusCode: e.response?.statusCode,
      );
    }
  }
}
