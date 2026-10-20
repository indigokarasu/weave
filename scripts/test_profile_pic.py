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

def test_gravatar():
    contact = {'email': 'test@example.com'}
    candidates = profile_pic.known_image_urls(contact)
    gravatar_urls = [u for src, u in candidates if src == 'gravatar']
    assert len(gravatar_urls) == 1, f"Expected 1 gravatar URL, got {len(gravatar_urls)}"
    assert 'gravatar.com' in gravatar_urls[0], "URL should contain gravatar.com"
    print("Gravatar test passed")

def test_candidate_sources():
    """Test that known_image_urls finds candidates from various platforms."""
    contact = {
        "id": "test",
        "name": "Test User",
        "avatar_url": None,
        "urls": [
            "https://github.com/testuser",
            "https://linkedin.com/in/testuser",
            "https://twitter.com/testuser",
            "https://reddit.com/user/testuser",
        ],
        "email": "test@example.com",
    }
    candidates = profile_pic.known_image_urls(contact)
    sources = [s for s, _ in candidates]
    print(f"Sources found: {sources}")
    assert "github" in sources
    assert "gravatar" in sources
    print("Candidate discovery tests passed")

if __name__ == "__main__":
    test_placeholder_filter()
    test_face_detection()
    test_image_quality()
    test_gravatar()
    test_candidate_sources()
    print("All tests passed!")