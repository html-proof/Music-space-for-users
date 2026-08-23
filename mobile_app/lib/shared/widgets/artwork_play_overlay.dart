import 'package:flutter/material.dart';

/// The small solid-black circular play button overlaid on the bottom-right
/// corner of artwork (album covers, home rail tiles) in the reference
/// design -- distinct from PrimaryPlayButton, which is the larger
/// white-on-dark button used for the main transport control on the
/// full-bleed player screen.
class ArtworkPlayOverlay extends StatelessWidget {
  const ArtworkPlayOverlay({super.key, required this.onTap, this.size = 36});

  final VoidCallback onTap;
  final double size;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: size,
        height: size,
        decoration: const BoxDecoration(color: Color(0xFF16161A), shape: BoxShape.circle),
        alignment: Alignment.center,
        child: Icon(Icons.play_arrow, color: Colors.white, size: size * 0.55),
      ),
    );
  }
}
