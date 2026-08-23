import 'package:flutter/material.dart';

/// The solid white circular play button used throughout the reference
/// design (album/playlist "play all", the transport control, the mini bar)
/// instead of a colored pill button -- high-contrast chrome, not a branded
/// action.
class PrimaryPlayButton extends StatelessWidget {
  const PrimaryPlayButton({
    super.key,
    required this.onPressed,
    this.icon = Icons.play_arrow,
    this.size = 48,
  });

  final VoidCallback? onPressed;
  final IconData icon;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: onPressed == null ? Colors.white.withValues(alpha: 0.4) : Colors.white,
        shape: BoxShape.circle,
      ),
      child: IconButton(
        padding: EdgeInsets.zero,
        iconSize: size * 0.5,
        color: Colors.black,
        icon: Icon(icon),
        onPressed: onPressed,
      ),
    );
  }
}
