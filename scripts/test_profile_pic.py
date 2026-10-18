import sys as _sys
if '--help' in _sys.argv or '-h' in _sys.argv:
    print('Usage: ' + __file__ + ' [--help]')
    _sys.exit(0)
#!/usr/bin/env python3
"""Tests for profile_pic module."""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(__file__))
os.environ['HERMES_HOME'] = '/root/.hermes/profiles/indigo'
os.environ['AGENT_ROOT'] = '/root/.hermes/profiles/indigo'

import profile_pic

def test_placeholder_filter():
    assert profile_pic._is_placeholder_url('https://en.gravatar.com/images/homepage/avatar-01.png') is True
    assert profile_pic._is_placeholder_url('https://example.com/photo.jpg') is False
    assert profile_pic._is_placeholder_url('https://i.pinimg.com/originals/94/0b/39/940b393d47acdef0dcf487d25294fd39.jpg') is False
    print("Placeholder filter tests passed")

def test_face_detection():
    # Test with a known image (Rachel Metz profile photo)
    url = "https://media.muckrack.com/profile/images/19711/rachelmetz.jpeg.256x256_q100_crop-smart.jpg"
    img = profile_pic.fetch_image_bytes(url)
    assert img is not None, "Failed to fetch image"
    face, conf, w, h = profile_pic.detect_face(img)
    assert face is True, "Face not detected"
    assert conf >= 0.5, f"Confidence too low: {conf}"
    assert w > 0 and h > 0, "Invalid dimensions"
    print(f"Face detection test passed: conf={conf}, w={w}, h={h}")

def test_image_quality():
    url = "https://media.muckrack.com/profile/images/19711/rachelmetz.jpeg.256x256_q100_crop-smart.jpg"
    img = profile_pic.fetch_image_bytes(url)
    sharp, color, photo = profile_pic.image_quality(img)
    assert 0 <= sharp <= 1, f"Sharpness out of range: {sharp}"
    assert 0 <= color <= 1, f"Color variance out of range: {color}"
    assert 0 <= photo <= 1, f"Photo guess out of range: {photo}"
    print(f"Image quality test passed: sharp={sharp}, color={color}, photo={photo}")

def test_searxng_image_search():
    results = profile_pic.searxng_image_search("Rachel Metz profile picture", limit=3)
    assert isinstance(results, list), "Should return a list"
    assert len(results) > 0, "Should return at least one result"
    print(f"SearXNG image search test passed: {len(results)} results")

if __name__ == "__main__":
    test_placeholder_filter()
    test_face_detection()
    test_image_quality()
    test_searxng_image_search()
    print("All tests passed!")