class AppConfig {
  // Android emulator -> host machine. Change to your computer's LAN IP for a physical phone.
  static const String apiBaseUrl = String.fromEnvironment('API_BASE_URL', defaultValue: 'http://10.0.2.2:8000/api');
}
