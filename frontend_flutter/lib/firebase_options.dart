import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart' show defaultTargetPlatform, kIsWeb, TargetPlatform;

class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    if (kIsWeb) {
      return web;
    }

    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return android;
      case TargetPlatform.iOS:
        return ios;
      case TargetPlatform.macOS:
        return macos;
      case TargetPlatform.windows:
        return windows;
      case TargetPlatform.linux:
        return linux;
      default:
        return android;
    }
  }

  static const FirebaseOptions web = FirebaseOptions(
    apiKey: 'AIzaSyAW0VA8BB9viHnMu3z6oxMuwMMT-68TdaI',
    appId: '1:891431822056:web:4504718aba3efcf5aa5f5d',
    messagingSenderId: '891431822056',
    projectId: 'hackathon-a7f37',
    authDomain: 'hackathon-a7f37.firebaseapp.com',
    storageBucket: 'hackathon-a7f37.firebasestorage.app',
    measurementId: 'G-58RG0GD5KK',
  );

  static const FirebaseOptions android = FirebaseOptions(
    apiKey: 'AIzaSyAW0VA8BB9viHnMu3z6oxMuwMMT-68TdaI',
    appId: '1:891431822056:web:4504718aba3efcf5aa5f5d',
    messagingSenderId: '891431822056',
    projectId: 'hackathon-a7f37',
    storageBucket: 'hackathon-a7f37.firebasestorage.app',
  );

  static const FirebaseOptions ios = FirebaseOptions(
    apiKey: 'AIzaSyAW0VA8BB9viHnMu3z6oxMuwMMT-68TdaI',
    appId: '1:891431822056:web:4504718aba3efcf5aa5f5d',
    messagingSenderId: '891431822056',
    projectId: 'hackathon-a7f37',
    storageBucket: 'hackathon-a7f37.firebasestorage.app',
    iosBundleId: 'com.example.hackathonproj',
  );

  static const FirebaseOptions macos = FirebaseOptions(
    apiKey: 'AIzaSyAW0VA8BB9viHnMu3z6oxMuwMMT-68TdaI',
    appId: '1:891431822056:web:4504718aba3efcf5aa5f5d',
    messagingSenderId: '891431822056',
    projectId: 'hackathon-a7f37',
    storageBucket: 'hackathon-a7f37.firebasestorage.app',
    iosBundleId: 'com.example.hackathonproj',
  );

  static const FirebaseOptions windows = FirebaseOptions(
    apiKey: 'AIzaSyAW0VA8BB9viHnMu3z6oxMuwMMT-68TdaI',
    appId: '1:891431822056:web:4504718aba3efcf5aa5f5d',
    messagingSenderId: '891431822056',
    projectId: 'hackathon-a7f37',
    authDomain: 'hackathon-a7f37.firebaseapp.com',
    storageBucket: 'hackathon-a7f37.firebasestorage.app',
  );

  static const FirebaseOptions linux = FirebaseOptions(
    apiKey: 'AIzaSyAW0VA8BB9viHnMu3z6oxMuwMMT-68TdaI',
    appId: '1:891431822056:web:4504718aba3efcf5aa5f5d',
    messagingSenderId: '891431822056',
    projectId: 'hackathon-a7f37',
    authDomain: 'hackathon-a7f37.firebaseapp.com',
    storageBucket: 'hackathon-a7f37.firebasestorage.app',
  );
}