import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
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
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 600),
          child: Container(
            margin: const EdgeInsets.all(16),
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 28),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Welcome to Disaster Tracker',
                  style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 12),
                Text(
                  'You are logged in and can now access all features.',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: 20),
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Quick Stats',
                        style: Theme.of(context).textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
                      ),
                      const SizedBox(height: 12),
                      const _StatRow(label: 'Active Alerts', value: '3'),
                      const _StatRow(label: 'Response Teams', value: '12'),
                      const _StatRow(label: 'Coverage Status', value: 'Stable'),
                    ],
                  ),
                ),
              ],
            ),
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

class _StatRow extends StatelessWidget {
  const _StatRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label),
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold)),
        ],
      ),
    );
  }
}
