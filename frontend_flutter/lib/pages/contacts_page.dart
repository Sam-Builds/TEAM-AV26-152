import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

class ContactsPage extends StatefulWidget {
  const ContactsPage({super.key});

  @override
  State<ContactsPage> createState() => _ContactsPageState();
}

class _ContactsPageState extends State<ContactsPage> {
  int _selectedIndex = 1;

  @override
  Widget build(BuildContext context) {
    final List<Map<String, dynamic>> navItems = [
      {'icon': Icons.home, 'label': 'Home', 'route': '/home'},
      {'icon': Icons.phone, 'label': 'Contacts', 'route': '/contacts'},
      {'icon': Icons.search, 'label': 'Search', 'route': '/search'},
      {'icon': Icons.settings, 'label': 'Settings', 'route': '/settings'},
    ];

    final List<Map<String, String>> authorities = [
      {
        'name': 'Emergency Services',
        'phone': '+234-1-2345-6789',
        'email': 'emergency@govt.ng',
        'type': 'Emergency'
      },
      {
        'name': 'National Disaster Management Agency',
        'phone': '+234-1-4567-8901',
        'email': 'info@ndma.gov.ng',
        'type': 'Disaster Management'
      },
      {
        'name': 'Red Cross Society',
        'phone': '+234-1-5678-9012',
        'email': 'info@redcross.ng',
        'type': 'Relief'
      },
      {
        'name': 'Fire Service',
        'phone': '+234-1-6789-0123',
        'email': 'info@fireng.gov.ng',
        'type': 'Emergency'
      },
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text('Emergency Contacts'),
        elevation: 2,
      ),
      body: ListView.builder(
        padding: const EdgeInsets.all(16),
        itemCount: authorities.length,
        itemBuilder: (context, index) {
          final contact = authorities[index];
          return Card(
            margin: const EdgeInsets.only(bottom: 12),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Expanded(
                        child: Text(
                          contact['name'] ?? '',
                          style: Theme.of(context).textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.bold),
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.secondary.withValues(alpha: 0.2),
                          borderRadius: BorderRadius.circular(20),
                        ),
                        child: Text(
                          contact['type'] ?? '',
                          style: const TextStyle(fontSize: 12),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  _ContactField(
                    icon: Icons.phone,
                    label: 'Phone',
                    value: contact['phone'] ?? '',
                  ),
                  const SizedBox(height: 8),
                  _ContactField(
                    icon: Icons.email,
                    label: 'Email',
                    value: contact['email'] ?? '',
                  ),
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          icon: const Icon(Icons.call),
                          label: const Text('Call'),
                          onPressed: () {
                            ScaffoldMessenger.of(context).showSnackBar(
                              const SnackBar(content: Text('Calling feature coming soon')),
                            );
                          },
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: ElevatedButton.icon(
                          icon: const Icon(Icons.mail),
                          label: const Text('Email'),
                          onPressed: () {
                            ScaffoldMessenger.of(context).showSnackBar(
                              const SnackBar(content: Text('Email feature coming soon')),
                            );
                          },
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          );
        },
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

class _ContactField extends StatelessWidget {
  const _ContactField({
    required this.icon,
    required this.label,
    required this.value,
  });

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, size: 18, color: Theme.of(context).colorScheme.primary),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: const TextStyle(fontSize: 12, color: Colors.grey)),
              Text(value, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
            ],
          ),
        ),
      ],
    );
  }
}
