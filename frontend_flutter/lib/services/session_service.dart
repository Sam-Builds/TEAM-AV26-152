import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

class SessionService {
  static const String _userKey = 'app_user';
  static const String _tokenKey = 'app_token';

  static Future<void> saveSession({
    required int id,
    required String username,
    required String email,
    String? token,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final userData = jsonEncode({
      'id': id,
      'username': username,
      'email': email,
    });
    await prefs.setString(_userKey, userData);
    if (token != null) {
      await prefs.setString(_tokenKey, token);
    }
  }

  static Future<Map<String, dynamic>?> getSession() async {
    final prefs = await SharedPreferences.getInstance();
    final userData = prefs.getString(_userKey);
    if (userData == null) return null;
    return jsonDecode(userData) as Map<String, dynamic>;
  }

  static Future<String?> getToken() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_tokenKey);
  }

  static Future<bool> isLoggedIn() async {
    final session = await getSession();
    return session != null;
  }

  static Future<void> clearSession() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_userKey);
    await prefs.remove(_tokenKey);
  }

  static Future<dynamic> getUser(String key) async {
    final session = await getSession();
    return session?[key];
  }
}
