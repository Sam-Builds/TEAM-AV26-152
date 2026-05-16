import 'dart:async';
import 'dart:convert';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:http/http.dart' as http;

import '../firebase_options.dart';
import 'session_service.dart';

const String _backendBaseUrl = 'https://api.samstack.site';

const AndroidNotificationChannel _androidNotificationChannel = AndroidNotificationChannel(
  'disaster_alerts',
  'Disaster Alerts',
  description: 'Notifications from the disaster alert backend',
  importance: Importance.max,
);

final FlutterLocalNotificationsPlugin _flutterLocalNotificationsPlugin = FlutterLocalNotificationsPlugin();

bool _fcmConfigured = false;

@pragma('vm:entry-point')
Future<void> firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  await _ensureFirebaseInitialized();

  final title = message.notification?.title ?? message.data['title']?.toString();
  final description = message.notification?.body ?? message.data['description']?.toString() ?? message.data['body']?.toString();

  if ((title == null || title.isEmpty) && (description == null || description.isEmpty)) {
    return;
  }

  final plugin = FlutterLocalNotificationsPlugin();
  const initializationSettings = InitializationSettings(
    android: AndroidInitializationSettings('@mipmap/ic_launcher'),
    iOS: DarwinInitializationSettings(),
  );

  await plugin.initialize(initializationSettings);
  await _showLocalNotification(
    plugin: plugin,
    title: title ?? 'Disaster Alert',
    body: description ?? '',
    payload: message.data,
  );
}

Future<void> configureFcm() async {
  if (_fcmConfigured) {
    return;
  }

  await _ensureFirebaseInitialized();

  FirebaseMessaging.onBackgroundMessage(firebaseMessagingBackgroundHandler);

  final messaging = FirebaseMessaging.instance;
  await messaging.requestPermission(alert: true, badge: true, sound: true);
  await messaging.setForegroundNotificationPresentationOptions(
    alert: true,
    badge: true,
    sound: true,
  );

  const initializationSettings = InitializationSettings(
    android: AndroidInitializationSettings('@mipmap/ic_launcher'),
    iOS: DarwinInitializationSettings(),
  );

  await _flutterLocalNotificationsPlugin.initialize(initializationSettings);

  final androidImplementation = _flutterLocalNotificationsPlugin.resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>();
  await androidImplementation?.createNotificationChannel(_androidNotificationChannel);
  await androidImplementation?.requestNotificationsPermission();

  FirebaseMessaging.onMessage.listen(_showForegroundMessage);
  FirebaseMessaging.instance.onTokenRefresh.listen((newToken) async {
    await registerTokenForCurrentSession(newToken);
  });
  FirebaseMessaging.onMessageOpenedApp.listen((message) {
    debugPrint('Notification opened: ${message.notification?.title}');
  });

  _fcmConfigured = true;
  await syncCurrentSessionFcmToken();
}

Future<void> _ensureFirebaseInitialized() async {
  if (Firebase.apps.isNotEmpty) {
    return;
  }

  await Firebase.initializeApp(options: DefaultFirebaseOptions.currentPlatform);
}

Future<void> syncCurrentSessionFcmToken() async {
  final session = await SessionService.getSession();
  if (session == null || session['id'] == null) {
    return;
  }

  final token = await _getTokenWithRetry();
  if (token == null || token.isEmpty) {
    return;
  }

  final storedToken = await SessionService.getToken();
  if (storedToken == token) {
    return;
  }

  await registerTokenForCurrentSession(token);
}

Future<bool> registerTokenForCurrentSession(String token) async {
  final session = await SessionService.getSession();
  if (session == null || session['id'] == null) {
    return false;
  }

  final userId = session['id'].toString();
  final username = (session['username'] ?? '').toString();
  final email = (session['email'] ?? '').toString();

  Object? lastError;
  for (var attempt = 0; attempt < 3; attempt++) {
    try {
      await _postFcmToken(userId, token);
      await SessionService.saveSession(
        id: session['id'] is int ? session['id'] as int : int.tryParse(userId) ?? 0,
        username: username,
        email: email,
        token: token,
      );
      return true;
    } catch (error) {
      lastError = error;
      if (attempt < 2) {
        await Future.delayed(Duration(milliseconds: 400 * (attempt + 1)));
      }
    }
  }

  debugPrint('Failed to sync FCM token: $lastError');
  return false;
}

Future<String?> _getTokenWithRetry() async {
  for (var attempt = 0; attempt < 3; attempt++) {
    try {
      final token = await FirebaseMessaging.instance.getToken().timeout(const Duration(seconds: 8));
      if (token != null && token.isNotEmpty) {
        return token;
      }
    } catch (_) {}

    if (attempt < 2) {
      await Future.delayed(Duration(milliseconds: 500 * (attempt + 1)));
    }
  }

  return null;
}

Future<void> _postFcmToken(String userId, String token) async {
  final response = await http
      .post(
        Uri.parse('$_backendBaseUrl/auth/update-fcm'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'user_id': userId, 'fcm_token': token}),
      )
      .timeout(const Duration(seconds: 8));

  if (response.statusCode < 200 || response.statusCode >= 300) {
    throw StateError('FCM sync failed with status ${response.statusCode}');
  }
}

Future<void> _showForegroundMessage(RemoteMessage message) async {
  final title = message.notification?.title ?? message.data['title']?.toString();
  final description = message.notification?.body ?? message.data['description']?.toString() ?? message.data['body']?.toString();

  if ((title == null || title.isEmpty) && (description == null || description.isEmpty)) {
    return;
  }

  await _showLocalNotification(
    plugin: _flutterLocalNotificationsPlugin,
    title: title ?? 'Disaster Alert',
    body: description ?? '',
    payload: message.data,
  );
}

Future<void> _showLocalNotification({
  required FlutterLocalNotificationsPlugin plugin,
  required String title,
  required String body,
  required Map<String, dynamic> payload,
}) async {
  final notificationDetails = NotificationDetails(
    android: AndroidNotificationDetails(
      _androidNotificationChannel.id,
      _androidNotificationChannel.name,
      channelDescription: _androidNotificationChannel.description,
      importance: Importance.max,
      priority: Priority.high,
      playSound: true,
    ),
    iOS: const DarwinNotificationDetails(presentAlert: true, presentBadge: true, presentSound: true),
  );

  await plugin.show(
    DateTime.now().millisecondsSinceEpoch ~/ 1000,
    title,
    body,
    notificationDetails,
    payload: jsonEncode(payload),
  );
}