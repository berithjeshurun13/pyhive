from typing import Literal
import numpy as np
import math
import random

def fade(t):
        return 6*t**5 - 15*t**4 + 10*t**3
    
def lerp(a, b, t):
        return a + t * (b - a)
    
def r_grad_vectors(hash_val):
    """Random gradient vectors (unit vectors)"""
    theta = 2 * np.pi * hash_val
    return np.array([np.cos(theta), np.sin(theta)])


def r_pseudo_seed(ix : int, iy : int , seed : int=0) -> int:
    """Hash function (pseudo-random but deterministic)"""
    return (ix * 374761393 + iy * 668265263 + seed * 1442695040888963407) & 0xffffffff

def _rotate(x, y, theta):
    ct = math.cos(theta)
    st = math.sin(theta)
    return x * ct + y * st, -x * st + y * ct

def _generate_points(width, height, points, seed=0):
    random.seed(seed)
    return [
        (random.uniform(0, width), random.uniform(0, height))
        for _ in range(points)
    ]



def perlin(x, y, seed=0):
    
    
    # Integer lattice coordinates
    x0 = int(np.floor(x))
    y0 = int(np.floor(y))
    x1 = x0 + 1
    y1 = y0 + 1

    # Local coordinates inside cell
    sx = x - x0
    sy = y - y0

    # Fade curves
    u = fade(sx)
    v = fade(sy)

    # Corner gradients
    g00 = r_grad_vectors(r_pseudo_seed(x0, y0, seed))
    g10 = r_grad_vectors(r_pseudo_seed(x1, y0, seed))
    g01 = r_grad_vectors(r_pseudo_seed(x0, y1, seed))
    g11 = r_grad_vectors(r_pseudo_seed(x1, y1, seed))

    # Distance vectors
    d00 = np.array([sx, sy])
    d10 = np.array([sx - 1, sy])
    d01 = np.array([sx, sy - 1])
    d11 = np.array([sx - 1, sy - 1])

    # Dot products (core Perlin idea)
    c00 = np.dot(g00, d00)
    c10 = np.dot(g10, d10)
    c01 = np.dot(g01, d01)
    c11 = np.dot(g11, d11)

    # Interpolation
    x0_interp = lerp(c00, c10, u)
    x1_interp = lerp(c01, c11, u)

    return lerp(x0_interp, x1_interp, v)

def noise_2d_perlin(shape, scale=50.0, seed=0):
    h, w = shape
    result = np.zeros((h, w))

    for i in range(h):
        for j in range(w):
            x = j / scale
            y = i / scale
            result[i, j] = perlin(x, y, seed)

    return result

def anisotropic_gabor(x, y, frequency, theta, sigma_x, sigma_y):
    x_theta = x * np.cos(theta) + y * np.sin(theta)
    y_theta = -x * np.sin(theta) + y * np.cos(theta)
    gaussian = np.exp(-(x_theta**2 / (2 * sigma_x**2) + y_theta**2 / (2 * sigma_y**2)))
    sinusoid = np.cos(2 * np.pi * frequency * x_theta)
    return gaussian * sinusoid

def noise_2d_fbm(shape, octaves=6, persistence=0.5, lacunarity=2.0, scale=50.0, seed=0):
    """ Fractal Brownian Motion """
    h, w = shape
    result = np.zeros((h, w))

    for i in range(h):
        for j in range(w):

            x = j / scale
            y = i / scale

            amplitude = 1.0
            frequency = 1.0
            total = 0.0
            max_value = 0.0

            for _ in range(octaves):
                total += amplitude * perlin(x * frequency, y * frequency, seed)
                max_value += amplitude

                amplitude *= persistence
                frequency *= lacunarity

            result[i, j] = total / max_value

    return result

def gabor(x, y, x0, y0, frequency, theta, sigma_x, sigma_y):
    # shift to kernel center
    dx = x - x0
    dy = y - y0

    # rotate
    x_theta, y_theta = _rotate(dx, dy, theta)

    # anisotropic Gaussian envelope
    gaussian = math.exp(
        -(x_theta**2 / (2 * sigma_x**2) +
          y_theta**2 / (2 * sigma_y**2))
    )

    # directional cosine wave
    sinusoid = math.cos(2 * math.pi * frequency * x_theta)

    return gaussian * sinusoid

def noise_2d_gabor(shape,
                kernels=50,
                frequency=0.1,
                sigma_x=5,
                sigma_y=5,
                seed=0):

    random.seed(seed)
    np.random.seed(seed)

    h, w = shape
    noise = np.zeros((h, w))

    # generate random kernels
    kernel_data = []

    for _ in range(kernels):
        x0 = random.uniform(0, w)
        y0 = random.uniform(0, h)
        theta = random.uniform(0, math.pi)
        amp = random.uniform(0.5, 1.0)

        kernel_data.append((x0, y0, theta, amp))

    # accumulate kernels
    for i in range(h):
        for j in range(w):

            value = 0.0

            for (x0, y0, theta, amp) in kernel_data:
                value += amp * gabor(
                    j, i,
                    x0, y0,
                    frequency,
                    theta,
                    sigma_x,
                    sigma_y
                )

            noise[i, j] = value

    return noise

def worley_noise(shape, points=50, seed=0, metric : Literal['euclidean', 'manhattan']="euclidean"):
    h, w = shape

    feature_points = _generate_points(w, h, points, seed)
    noise = np.zeros((h, w))

    for i in range(h):
        for j in range(w):

            min_dist = float("inf")

            for (px, py) in feature_points:

                dx = j - px
                dy = i - py

                if metric == "manhattan":
                    dist = abs(dx) + abs(dy)
                else:
                    dist = math.sqrt(dx*dx + dy*dy)

                if dist < min_dist:
                    min_dist = dist

            noise[i, j] = min_dist

    # normalize
    noise = noise / np.max(noise)
    return noise

