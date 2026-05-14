import 'package:flutter/material.dart';

void main() {
  runApp(const DisasterApp());
}

class DisasterApp extends StatelessWidget {
  const DisasterApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Disaster Tracker',
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.red),
      home: const MapScreen(), // This will be our only screen
    );
  }
}

// We will build this next
class MapScreen extends StatefulWidget {
  const MapScreen({super.key});

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(child: Text("Map Loading...")),
    );
  }
}