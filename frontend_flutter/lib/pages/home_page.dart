import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:geolocator/geolocator.dart';
import 'dart:convert';
import '../services/session_service.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  int _selectedIndex = 0;
  String _userEmail = '';

  @override
  void initState() {
    super.initState();
    _loadUserEmail();
  }

  Future<void> _loadUserEmail() async {
    final email = await SessionService.getUser('email');
    if (mounted) {
      setState(() => _userEmail = email?.toString() ?? '');
    }
  }

  Future<void> _logout() async {
    await SessionService.clearSession();
    if (mounted) {
      context.go('/');
    }
  }

  @override
  Widget build(BuildContext context) {
    final List<Map<String, dynamic>> navItems = [
      {'icon': Icons.home, 'label': 'Home', 'route': '/home'},
      {'icon': Icons.phone, 'label': 'Contacts', 'route': '/contacts'},
      {'icon': Icons.search, 'label': 'Search', 'route': '/search'},
      {'icon': Icons.settings, 'label': 'Settings', 'route': '/settings'},
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text('Home'),
        elevation: 2,
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Center(
              child: Text(
                _userEmail,
                style: const TextStyle(fontSize: 12, color: Colors.white70),
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: _logout,
          ),
        ],
      ),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const AlertCard(),
              const SizedBox(height: 16),
              const WeatherCard(),
              const SizedBox(height: 16),
              const NewsCard(),
              const SizedBox(height: 16),
            ],
          ),
        ),
      ),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _selectedIndex,
        type: BottomNavigationBarType.fixed,
        onTap: (index) {
          setState(() => _selectedIndex = index);
          context.go(navItems[index]['route']);
        },
        items: [
          for (var item in navItems)
            BottomNavigationBarItem(
              icon: Icon(item['icon']),
              label: item['label'],
            ),
        ],
      ),
    );
  }
}

class AlertCard extends StatefulWidget {
  const AlertCard({super.key});

  @override
  State<AlertCard> createState() => _AlertCardState();
}

class _AlertCardState extends State<AlertCard> {
  late Future<List<dynamic>> alertsFuture;

  @override
  void initState() {
    super.initState();
    alertsFuture = _fetchAlerts();
  }

  Future<List<dynamic>> _fetchAlerts() async {
    try {
      // Get device location for proximity-based alerts
      double latitude = 29.76;
      double longitude = -95.38;
      try {
        final position = await Geolocator.getCurrentPosition(
          desiredAccuracy: LocationAccuracy.medium,
        ).timeout(const Duration(seconds: 5));
        latitude = position.latitude;
        longitude = position.longitude;
        debugPrint('Alerts: Using device location: $latitude, $longitude');
      } catch (locError) {
        debugPrint('Alerts: Location error, using fallback (Houston): $locError');
      }

      const String url = 'https://api.samstack.site/api/alerts';
      final Map<String, dynamic> requestBody = {
        'query': 'flood',
        'mode': 'mock',
        'userLat': latitude,
        'userLng': longitude,
      };

      final response = await http.post(
        Uri.parse(url),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(requestBody),
      ).timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        debugPrint('Alert response: $data');

        final alert = data is Map ? data['alert'] : null;
        if (alert is Map) {
          final alertMap = Map<String, dynamic>.from(alert);
          final alertTitle = (alertMap['title'] as String?)?.trim() ?? '';
          final alertDescription = (alertMap['description'] as String?)?.trim() ?? '';
          if (alertTitle.isNotEmpty || alertDescription.isNotEmpty) {
            debugPrint('Alert summary: $alertTitle | $alertDescription');
          }
        }
        
        // Parse .NET backend response: { success: true, data: { threatClusters: [...] } }
        List<dynamic> clusters = [];
        if (data is Map) {
          final map = Map<String, dynamic>.from(data);
          final alert = map['alert'];
          if (alert is Map) {
            final alertMap = Map<String, dynamic>.from(alert);
            debugPrint('Alert title: ${alertMap['title']}, description: ${alertMap['description']}');
          }
          // Extract data.threatClusters (PascalCase from .NET backend)
          if (map.containsKey('data')) {
            final inner = map['data'];
            if (inner is Map) {
              final innerMap = Map<String, dynamic>.from(inner);
              if (innerMap.containsKey('threatClusters')) {
                try {
                  clusters = List<dynamic>.from(innerMap['threatClusters'] as List);
                } catch (_) {}
              } else if (innerMap.containsKey('threat_clusters')) {
                try {
                  clusters = List<dynamic>.from(innerMap['threat_clusters'] as List);
                } catch (_) {}
              }
            }
          }
        }

        return clusters;
      }

      return [];
    } catch (e) {
      debugPrint('Error fetching alerts: $e');
      return [];
    }
  }

  String _formatClusterSummary(Map<String, dynamic> cluster) {
    final title = (cluster['primaryThreat'] as String?)?.trim();
    final summary = (cluster['summary'] as String?)?.trim();
    final severity = (cluster['aggregatedSeverity'] as num?)?.toDouble() ?? 0.0;

    if (title != null && title.isNotEmpty && summary != null && summary.isNotEmpty) {
      return '$title: $summary';
    }

    if (title != null && title.isNotEmpty) {
      return title;
    }

    if (summary != null && summary.isNotEmpty) {
      return summary;
    }

    return 'Threat Alert (Severity: ${(severity * 100).toStringAsFixed(0)}%)';
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: Container(
        height: 300,
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.1),
              blurRadius: 4,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                'Active Alerts',
                style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.bold),
              ),
            ),
            Expanded(
              child: FutureBuilder<List<dynamic>>(
                future: alertsFuture,
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  if (snapshot.hasError) {
                    return Center(child: Text('Error: ${snapshot.error}'));
                  }
                  final alerts = snapshot.data ?? [];
                  if (alerts.isEmpty) {
                    return const Center(child: Text('No alerts'));
                  }
                  return Scrollbar(
                    child: ListView.builder(
                      itemCount: alerts.length,
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      itemBuilder: (context, index) {
                        final alert = alerts[index] as Map<String, dynamic>;
                        final severity = (alert['aggregatedSeverity'] as num?)?.toDouble() ?? 0.0;
                        final polygonList = alert['dangerPolygon'] as List? ?? [];
                        final title = (alert['primaryThreat'] as String?)?.trim();
                        final description = (alert['summary'] as String?)?.trim();
                        final summary = _formatClusterSummary(alert);

                        return Container(
                          width: double.infinity,
                          margin: const EdgeInsets.symmetric(vertical: 6),
                          padding: const EdgeInsets.all(10),
                          decoration: BoxDecoration(
                            color: Colors.grey[100],
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                title != null && title.isNotEmpty ? title : summary,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(height: 4),
                              if (description != null && description.isNotEmpty) ...[
                                Text(
                                  description,
                                  maxLines: 3,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(fontSize: 10, color: Colors.black87),
                                ),
                                const SizedBox(height: 4),
                              ],
                              Row(
                                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                                children: [
                                  Text(
                                    'ID: ${index + 1}',
                                    style: const TextStyle(fontSize: 9, color: Colors.grey),
                                  ),
                                  Text(
                                    'Severity: ${(severity * 100).toStringAsFixed(0)}%',
                                    style: const TextStyle(fontSize: 9, color: Colors.grey),
                                  ),
                                ],
                              ),
                              if (polygonList.isNotEmpty)
                                Padding(
                                  padding: const EdgeInsets.only(top: 4),
                                  child: Text(
                                    'Affected area: ${polygonList.length} coordinates',
                                    style: const TextStyle(fontSize: 9, color: Colors.grey),
                                  ),
                                ),
                            ],
                          ),
                        );
                      },
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class WeatherCard extends StatefulWidget {
  const WeatherCard({super.key});

  @override
  State<WeatherCard> createState() => _WeatherCardState();
}

class _WeatherCardState extends State<WeatherCard> {
  late Future<Map<String, dynamic>> weatherFuture;

  @override
  void initState() {
    super.initState();
    weatherFuture = _fetchWeather();
  }

  Future<Map<String, dynamic>> _fetchWeather() async {
    try {
      // Try to get device location, fall back to mock if it fails
      double latitude = 29.76;
      double longitude = -95.38;

      try {
        final position = await Geolocator.getCurrentPosition(
          desiredAccuracy: LocationAccuracy.medium,
        ).timeout(const Duration(seconds: 5));
        latitude = position.latitude;
        longitude = position.longitude;
        debugPrint('Weather: Using device location: $latitude, $longitude');
      } catch (locError) {
        debugPrint('Weather: Location permission/error, using fallback (Houston): $locError');
      }

      final apiKey = 'e4102a474c22968c1047cd213e684652';
      final response = await http.get(
        Uri.parse(
          'https://api.openweathermap.org/data/2.5/weather?lat=$latitude&lon=$longitude&appid=$apiKey&units=metric',
        ),
      ).timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        debugPrint('Weather response: $data');
        return data;
      }
      debugPrint('Weather API error: ${response.statusCode}');
      return {};
    } catch (e) {
      debugPrint('Error fetching weather: $e');
      return {};
    }
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: Container(
        height: 300,
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.1),
              blurRadius: 4,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                'Weather',
                style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.bold),
              ),
            ),
            Expanded(
              child: FutureBuilder<Map<String, dynamic>>(
                future: weatherFuture,
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  if (snapshot.hasError) {
                    return Center(child: Text('Error: ${snapshot.error}'));
                  }
                  final weather = snapshot.data ?? {};
                  if (weather.isEmpty) {
                    return const Center(child: Text('No weather data'));
                  }
                  final main = weather['main'] ?? {};
                  final weatherList = weather['weather'] as List? ?? [];
                  final weatherDesc = weatherList.isNotEmpty ? weatherList[0]['main'] ?? 'N/A' : 'N/A';
                  return Scrollbar(
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      child: Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                          color: Colors.grey[100],
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'Location: ${weather['name'] ?? 'Unknown'}',
                              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
                            ),
                            const SizedBox(height: 8),
                            Text('Temperature: ${main['temp']}°C', style: const TextStyle(fontSize: 11)),
                            Text('Feels like: ${main['feels_like']}°C', style: const TextStyle(fontSize: 11)),
                            Text('Condition: $weatherDesc', style: const TextStyle(fontSize: 11)),
                            Text('Humidity: ${main['humidity']}%', style: const TextStyle(fontSize: 11)),
                            Text('Pressure: ${main['pressure']} hPa', style: const TextStyle(fontSize: 11)),
                            const SizedBox(height: 8),
                            Text('Wind: ${weather['wind']?['speed']} m/s', style: const TextStyle(fontSize: 11)),
                            Text('Clouds: ${weather['clouds']?['all']}%', style: const TextStyle(fontSize: 11)),
                          ],
                        ),
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class NewsCard extends StatefulWidget {
  const NewsCard({super.key});

  @override
  State<NewsCard> createState() => _NewsCardState();
}

class _NewsCardState extends State<NewsCard> {
  late Future<List<dynamic>> newsFuture;

  @override
  void initState() {
    super.initState();
    newsFuture = _fetchNews();
  }

  Future<List<dynamic>> _fetchNews() async {
    try {
      final apiKey = '4c0b7401f8bf473285c5beb3db4b8592';
      final response = await http.get(
        Uri.parse(
          'https://newsapi.org/v2/everything?q=disaster&sortBy=publishedAt&apiKey=$apiKey&pageSize=10',
        ),
      ).timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        return data['articles'] ?? [];
      }
      return [];
    } catch (e) {
      debugPrint('Error fetching news: $e');
      return [];
    }
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: Container(
        height: 300,
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.1),
              blurRadius: 4,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                'News',
                style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.bold),
              ),
            ),
            Expanded(
              child: FutureBuilder<List<dynamic>>(
                future: newsFuture,
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  if (snapshot.hasError) {
                    return Center(child: Text('Error: ${snapshot.error}'));
                  }
                  final articles = snapshot.data ?? [];
                  if (articles.isEmpty) {
                    return const Center(child: Text('No news available'));
                  }
                  return Scrollbar(
                    child: ListView.builder(
                      itemCount: articles.length,
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      itemBuilder: (context, index) {
                        final article = articles[index];
                        return Container(
                          width: double.infinity,
                          margin: const EdgeInsets.symmetric(vertical: 6),
                          padding: const EdgeInsets.all(10),
                          decoration: BoxDecoration(
                            color: Colors.grey[100],
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                article['title'] ?? 'No title',
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                article['source']?['name'] ?? 'Unknown source',
                                style: const TextStyle(fontSize: 10, color: Colors.grey),
                              ),
                              const SizedBox(height: 2),
                              Text(
                                article['publishedAt'] ?? '',
                                style: const TextStyle(fontSize: 9, color: Colors.grey),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}
