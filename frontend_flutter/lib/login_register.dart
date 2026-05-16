import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;

import 'services/fcm_service.dart';
import 'services/session_service.dart';

const String _backendBaseUrl = 'https://api.samstack.site';

String _extractMessage(http.Response response, String fallback) {
  try {
    final decoded = jsonDecode(response.body);
    if (decoded is Map<String, dynamic>) {
      if (decoded['message'] is String) return decoded['message'] as String;

      if (decoded['error'] is String) return decoded['error'] as String;
      if (decoded['detail'] is String) return decoded['detail'] as String;
      return decoded.toString();
    }
  } catch (_) {

  }

  final body = response.body.trim();
  if (body.isNotEmpty) return '(${response.statusCode}) $body';
  return '(${response.statusCode}) $fallback';
}

void _showSnack(BuildContext context, String message) {
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _emailCtrl = TextEditingController();
  final TextEditingController _passCtrl = TextEditingController();
  bool _isLoading = false;

  @override
  void dispose() {
    _emailCtrl.dispose();
    _passCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final email = _emailCtrl.text.trim();
    final pass = _passCtrl.text;
    if (email.isEmpty || pass.isEmpty) {
      _showSnack(context, 'Enter email and password');
      return;
    }

    setState(() => _isLoading = true);
    try {
      final response = await http.post(
        Uri.parse('$_backendBaseUrl/auth/login'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'email': email, 'password': pass}),
      );

      if (!mounted) {
        return;
      }

      if (response.statusCode == 200) {
        _showSnack(context, 'Login successful');

        final userData = jsonDecode(response.body);
        if (userData['data'] != null) {
          await SessionService.saveSession(
            id: userData['data']['id'] ?? 0,
            username: userData['data']['username'] ?? '',
            email: userData['data']['email'] ?? '',
          );
          await syncCurrentSessionFcmToken();
        }
        if (mounted) {
          context.go('/home');
        }
      } else {
        _showSnack(context, _extractMessage(response, 'Login failed'));
      }
    } catch (_) {
      if (mounted) {
        _showSnack(context, 'Network error. Please try again.');
      }
    } finally {
      if (mounted) {
        setState(() => _isLoading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 28),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text('Welcome back', style: theme.textTheme.headlineLarge?.copyWith(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 6),
                  Text('Sign in to your account', style: theme.textTheme.bodyMedium),
                  const SizedBox(height: 18),
                  TextField(
                    controller: _emailCtrl,
                    decoration: InputDecoration(
                      labelText: 'Email',
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                    ),
                    keyboardType: TextInputType.emailAddress,
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _passCtrl,
                    decoration: InputDecoration(
                      labelText: 'Password',
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                    ),
                    obscureText: true,
                  ),
                  const SizedBox(height: 20),
                  ElevatedButton(
                    onPressed: _isLoading ? null : _submit,
                    child: _isLoading
                        ? const SizedBox(
                            height: 18,
                            width: 18,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                          )
                        : const Text('Sign in'),
                  ),
                  const SizedBox(height: 12),
                  Center(
                    child: TextButton(onPressed: () {}, child: const Text('Forgot password?')),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final TextEditingController _emailCtrl = TextEditingController();
  final TextEditingController _phoneCtrl = TextEditingController();
  final TextEditingController _passCtrl = TextEditingController();
  final TextEditingController _codeCtrl = TextEditingController();

  bool _isSendingCode = false;
  bool _isCreatingAccount = false;
  bool _codeSent = false;
  int _resendSeconds = 0;
  Timer? _resendTimer;

  @override
  void dispose() {
    _emailCtrl.dispose();
    _phoneCtrl.dispose();
    _passCtrl.dispose();
    _codeCtrl.dispose();
    _resendTimer?.cancel();
    super.dispose();
  }

  bool _looksLikePhone(String value) {
    return RegExp(r'^\+[1-9][0-9]{7,14}$').hasMatch(value);
  }

  String _buildUsernameFromEmail(String email) {
    final local = email.split('@').first.replaceAll(RegExp(r'[^a-zA-Z0-9_]'), '_');
    final suffix = DateTime.now().millisecondsSinceEpoch.toString().substring(8);
    return '${local}_$suffix';
  }

  void _startResendCooldown([int seconds = 30]) {
    _resendTimer?.cancel();
    setState(() => _resendSeconds = seconds);
    _resendTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      if (_resendSeconds <= 1) {
        timer.cancel();
        setState(() => _resendSeconds = 0);
      } else {
        setState(() => _resendSeconds -= 1);
      }
    });
  }

  Future<void> _sendCode({bool isResend = false}) async {
    final phone = _phoneCtrl.text.trim();
    if (!_looksLikePhone(phone)) {
      _showSnack(context, 'Use phone with country code, e.g. +2348012345678');
      return;
    }

    setState(() => _isSendingCode = true);
    try {
      final response = await http.post(
        Uri.parse('$_backendBaseUrl/auth/send-code'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'phone': phone}),
      );

      if (!mounted) {
        return;
      }

      if (response.statusCode == 200 || response.statusCode == 201) {
        setState(() => _codeSent = true);
        _startResendCooldown();
        _showSnack(context, isResend ? 'Code resent' : 'Verification code sent');
      } else {
        _showSnack(context, _extractMessage(response, 'Could not send verification code'));
      }
    } catch (_) {
      if (mounted) {
        _showSnack(context, 'Network error. Please try again.');
      }
    } finally {
      if (mounted) {
        setState(() => _isSendingCode = false);
      }
    }
  }

  Future<void> _verifyAndCreate() async {
    final email = _emailCtrl.text.trim();
    final phone = _phoneCtrl.text.trim();
    final pass = _passCtrl.text;
    final code = _codeCtrl.text.trim();

    if (email.isEmpty || !_looksLikePhone(phone) || pass.length < 8) {
      _showSnack(context, 'Enter a valid email, phone and password (8+ chars)');
      return;
    }
    if (!_codeSent) {
      _showSnack(context, 'Send verification code first');
      return;
    }
    if (code.length < 4) {
      _showSnack(context, 'Enter the verification code');
      return;
    }

    setState(() => _isCreatingAccount = true);
    try {
      debugPrint('Verifying code for phone: $phone');
      final verifyResponse = await http.post(
        Uri.parse('$_backendBaseUrl/auth/verify-code'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'phone': phone, 'code': code}),
      ).timeout(const Duration(seconds: 10));

      if (!mounted) {
        return;
      }

      debugPrint('Verify response status: ${verifyResponse.statusCode}, body: ${verifyResponse.body}');

      if (verifyResponse.statusCode != 200) {
        _showSnack(context, _extractMessage(verifyResponse, 'Invalid verification code'));
        return;
      }

      final registerResponse = await http.post(
        Uri.parse('$_backendBaseUrl/auth/register'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({
          'username': _buildUsernameFromEmail(email),
          'email': email,
          'phone': phone,
          'password': pass,
        }),
      ).timeout(const Duration(seconds: 10));

      if (!mounted) {
        return;
      }

      if (registerResponse.statusCode == 201 || registerResponse.statusCode == 200) {
        _showSnack(context, 'Registration complete');

        final userData = jsonDecode(registerResponse.body);
        if (userData['data'] != null) {
          await SessionService.saveSession(
            id: userData['data']['id'] ?? 0,
            username: userData['data']['username'] ?? '',
            email: userData['data']['email'] ?? '',
          );
          await syncCurrentSessionFcmToken();
        }
        if (mounted) {
          context.go('/home');
        }
      } else {
        _showSnack(context, _extractMessage(registerResponse, 'Registration failed'));
      }
    } catch (e) {
      debugPrint('Registration error: $e');
      if (mounted) {
        _showSnack(context, 'Error: ${e.toString()}');
      }
    } finally {
      if (mounted) {
        setState(() => _isCreatingAccount = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 28),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text('Create account', style: theme.textTheme.headlineLarge?.copyWith(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 6),
                  Text('Use email, phone and password, then verify SMS code', style: theme.textTheme.bodyMedium),
                  const SizedBox(height: 18),
                  TextField(
                    controller: _emailCtrl,
                    decoration: InputDecoration(
                      labelText: 'Email',
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                    ),
                    keyboardType: TextInputType.emailAddress,
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _phoneCtrl,
                    decoration: InputDecoration(
                      labelText: 'Phone (+countrycode)',
                      hintText: '+2348012345678',
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                    ),
                    keyboardType: TextInputType.phone,
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _passCtrl,
                    decoration: InputDecoration(
                      labelText: 'Password',
                      hintText: 'At least 8 characters',
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                    ),
                    obscureText: true,
                  ),
                  const SizedBox(height: 12),
                  if (_codeSent) ...[
                    TextField(
                      controller: _codeCtrl,
                      decoration: InputDecoration(
                        labelText: 'Verification code',
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                      ),
                      keyboardType: TextInputType.number,
                    ),
                    const SizedBox(height: 10),
                  ],
                  ElevatedButton(
                    onPressed: _isSendingCode || (_resendSeconds > 0 && _codeSent)
                        ? null
                        : () => _sendCode(isResend: _codeSent),
                    child: _isSendingCode
                        ? const SizedBox(
                            height: 18,
                            width: 18,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                          )
                        : Text(_codeSent
                            ? (_resendSeconds > 0 ? 'Resend in ${_resendSeconds}s' : 'Resend code')
                            : 'Send code'),
                  ),
                  const SizedBox(height: 20),
                  ElevatedButton(
                    onPressed: _isCreatingAccount ? null : _verifyAndCreate,
                    child: _isCreatingAccount
                        ? const SizedBox(
                            height: 18,
                            width: 18,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                          )
                        : const Text('Verify and create account'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

