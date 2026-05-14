import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

class SearchPage extends StatefulWidget {
  const SearchPage({super.key});

  @override
  State<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends State<SearchPage> {
  int _selectedIndex = 2;
  final TextEditingController _searchCtrl = TextEditingController();
  List<String> _searchResults = [];

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  void _performSearch(String query) {
    if (query.isEmpty) {
      setState(() => _searchResults = []);
      return;
    }

    final sampleResults = [
      'Flood Alert in Lagos',
      'Earthquake Report - Southern Region',
      'Landslide Warning Eastern Zone',
      'Windstorm Update Central Area',
      'Health Advisory Heat Wave',
    ];

    setState(() {
      _searchResults = sampleResults
          .where((item) => item.toLowerCase().contains(query.toLowerCase()))
          .toList();
    });
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
        title: const Text('Search Incidents'),
        elevation: 2,
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: TextField(
              controller: _searchCtrl,
              decoration: InputDecoration(
                hintText: 'Search incidents, alerts, updates...',
                prefixIcon: const Icon(Icons.search),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
              ),
              onChanged: _performSearch,
            ),
          ),
          Expanded(
            child: _searchResults.isEmpty
                ? Center(
                    child: Text(
                      _searchCtrl.text.isEmpty
                          ? 'Start searching for incidents'
                          : 'No results found',
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color: Colors.grey,
                          ),
                    ),
                  )
                : ListView.builder(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    itemCount: _searchResults.length,
                    itemBuilder: (context, index) {
                      return Card(
                        margin: const EdgeInsets.only(bottom: 12),
                        child: ListTile(
                          leading: Icon(
                            Icons.warning_amber_rounded,
                            color: Theme.of(context).colorScheme.secondary,
                          ),
                          title: Text(_searchResults[index]),
                          subtitle: const Text('Tap to view details'),
                          trailing: const Icon(Icons.arrow_forward, size: 18),
                          onTap: () {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text('Detail: ${_searchResults[index]}')),
                            );
                          },
                        ),
                      );
                    },
                  ),
          ),
        ],
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
