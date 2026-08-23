/// Mirrors GET /api/onboarding/languages (app/models/language.py).
class LanguageOption {
  final String id;
  final String name;
  final String code;

  const LanguageOption({required this.id, required this.name, required this.code});

  factory LanguageOption.fromJson(Map<String, dynamic> json) {
    return LanguageOption(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      code: json['code']?.toString() ?? '',
    );
  }
}
