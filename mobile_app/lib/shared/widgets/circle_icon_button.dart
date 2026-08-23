import 'package:flutter/material.dart';

/// A small icon inside a circular background chip -- the reference design's
/// consistent chrome for back/menu buttons on app bars and secondary
/// actions (shuffle, like) next to artwork, instead of a plain bare
/// IconButton with no background.
class CircleIconButton extends StatelessWidget {
  const CircleIconButton({
    super.key,
    required this.icon,
    required this.onPressed,
    this.background,
    this.iconColor,
    this.size = 40,
  });

  final IconData icon;
  final VoidCallback? onPressed;
  final Color? background;
  final Color? iconColor;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: background ?? Theme.of(context).colorScheme.surface,
        shape: BoxShape.circle,
      ),
      child: IconButton(
        padding: EdgeInsets.zero,
        iconSize: size * 0.5,
        icon: Icon(icon),
        color: iconColor,
        onPressed: onPressed,
      ),
    );
  }
}
