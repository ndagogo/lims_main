import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'config.dart';

class AuthException implements Exception {
  final String message;
  AuthException(this.message);
  @override String toString() => message;
}

class AuthService {
  AuthService({http.Client? client}) : _client = client ?? http.Client();
  final http.Client _client;
  static const _storage = FlutterSecureStorage();
  static const _accessKey = 'hfdn_access_token';
  static const _refreshKey = 'hfdn_refresh_token';
  static const _profileKey = 'hfdn_user_profile';

  Future<Map<String, dynamic>> login(String username, String password) async {
    final response = await _client.post(Uri.parse('${AppConfig.apiBaseUrl}/auth/login/'),
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: jsonEncode({'username': username.trim(), 'password': password}));
    final body = _decode(response);
    if (response.statusCode != 200) throw AuthException(body['detail']?.toString() ?? 'Unable to sign in.');
    await _storage.write(key: _accessKey, value: body['access'] as String);
    await _storage.write(key: _refreshKey, value: body['refresh'] as String);
    final profile = Map<String, dynamic>.from(body['user'] as Map);
    await _storage.write(key: _profileKey, value: jsonEncode(profile));
    return profile;
  }

  Future<Map<String, dynamic>?> currentProfile() async {
    final raw = await _storage.read(key: _profileKey);
    if (raw == null) return null;
    return Map<String, dynamic>.from(jsonDecode(raw) as Map);
  }

  Future<String?> accessToken() => _storage.read(key: _accessKey);

  Future<void> logout() async {
    await _storage.delete(key: _accessKey);
    await _storage.delete(key: _refreshKey);
    await _storage.delete(key: _profileKey);
  }

  Future<Map<String, dynamic>> fetchMe() async {
    final token = await accessToken();
    if (token == null) throw AuthException('Your session has expired. Please sign in again.');
    final response = await _client.get(Uri.parse('${AppConfig.apiBaseUrl}/auth/me/'),
      headers: {'Authorization': 'Bearer $token', 'Accept': 'application/json'});
    final body = _decode(response);
    if (response.statusCode != 200) throw AuthException('Could not load your profile. Please sign in again.');
    await _storage.write(key: _profileKey, value: jsonEncode(body));
    return body;
  }

  Map<String, dynamic> _decode(http.Response response) {
    try { return Map<String, dynamic>.from(jsonDecode(response.body) as Map); }
    catch (_) { return {'detail': 'Unexpected response from the server (${response.statusCode}).'}; }
  }
}
