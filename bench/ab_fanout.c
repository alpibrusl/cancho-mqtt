/* A saturated QoS 0 fan-out, to compare two builds of the broker on one machine (docs/benchmark.md, "A and B").
 *
 *     gcc -O2 -o ab_fanout bench/ab_fanout.c
 *     ./ab_fanout PORT [seconds] [publishers] [subscribers]
 *
 * Every subscriber subscribes to `bench/#`; every publisher writes PUBLISH packets of one size to `bench/t` as fast as the
 * broker takes them. All packets a subscriber receives are the same length, so a delivery is bytes read divided by that
 * length. One thread and one epoll: it is a comparison tool, not a benchmark of a broker against others; the load generator
 * may be the limit, so the program also prints the deliveries a second and leaves the broker's CPU to the caller to read.
 * Prints one line: deliveries_per_s published_per_s.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define PAYLOAD 16
static const char TOPIC[] = "bench/t";
#define PKT (2 + 2 + (int)sizeof(TOPIC) - 1 + PAYLOAD) /* fixed header 2, topic length 2, topic, payload */

static int dial(int port) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(port)};
    inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
    if (connect(s, (struct sockaddr *)&a, sizeof a) != 0) { perror("connect"); exit(1); }
    int one = 1;
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    return s;
}

static void send_all(int s, const unsigned char *p, int n) {
    while (n > 0) { int w = write(s, p, n); if (w <= 0) { perror("write"); exit(1); } p += w; n -= w; }
}

static void connect_packet(int s, const char *id) {
    unsigned char b[64]; int n = 0, il = strlen(id);
    b[n++] = 0x10; b[n++] = 10 + 2 + il;
    b[n++] = 0; b[n++] = 4; memcpy(b + n, "MQTT", 4); n += 4; b[n++] = 4; b[n++] = 2; b[n++] = 0; b[n++] = 0;
    b[n++] = 0; b[n++] = il; memcpy(b + n, id, il); n += il;
    send_all(s, b, n);
    unsigned char ack[4]; int got = 0;
    while (got < 4) { int r = read(s, ack + got, 4 - got); if (r <= 0) { perror("connack"); exit(1); } got += r; }
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: ab_fanout PORT [seconds] [publishers] [subscribers]\n"); return 2; }
    int port = atoi(argv[1]), secs = argc > 2 ? atoi(argv[2]) : 6, npub = argc > 3 ? atoi(argv[3]) : 4, nsub = argc > 4 ? atoi(argv[4]) : 100;
    int *sub = malloc(nsub * sizeof(int)), *pub = malloc(npub * sizeof(int));
    char id[32];
    for (int i = 0; i < nsub; i++) {
        sub[i] = dial(port); snprintf(id, sizeof id, "s%d", i); connect_packet(sub[i], id);
        unsigned char sb[] = {0x82, 2 + 2 + 7 + 2 + 1 - 2 + 0, 0, 1, 0, 7, 'b', 'e', 'n', 'c', 'h', '/', '#', 0};
        /* length: packet id 2 + topic length 2 + "bench/#" 7 + qos 1 = 12 */
        sb[1] = 12; send_all(sub[i], sb, 14);
        unsigned char sa[5]; int got = 0;
        while (got < 5) { int r = read(sub[i], sa + got, 5 - got); if (r <= 0) { perror("suback"); exit(1); } got += r; }
    }
    for (int i = 0; i < npub; i++) { pub[i] = dial(port); snprintf(id, sizeof id, "p%d", i); connect_packet(pub[i], id); }
    /* A buffer of whole packets for the publishers. */
    int per = 2000, blen = per * PKT;
    unsigned char *buf = malloc(blen);
    for (int k = 0; k < per; k++) {
        unsigned char *q = buf + k * PKT;
        q[0] = 0x30; q[1] = PKT - 2; q[2] = 0; q[3] = sizeof(TOPIC) - 1; memcpy(q + 4, TOPIC, sizeof(TOPIC) - 1);
        memset(q + 4 + sizeof(TOPIC) - 1, 'x', PAYLOAD);
    }
    int ep = epoll_create1(0);
    for (int i = 0; i < nsub; i++) { fcntl(sub[i], F_SETFL, O_NONBLOCK); struct epoll_event e = {.events = EPOLLIN, .data.u64 = i}; epoll_ctl(ep, EPOLL_CTL_ADD, sub[i], &e); }
    int *off = calloc(npub, sizeof(int)); long published = 0, bytes = 0;
    for (int i = 0; i < npub; i++) { fcntl(pub[i], F_SETFL, O_NONBLOCK); struct epoll_event e = {.events = EPOLLOUT, .data.u64 = 1000000 + i}; epoll_ctl(ep, EPOLL_CTL_ADD, pub[i], &e); }
    static unsigned char rb[1 << 16];
    struct timespec t0, t; clock_gettime(CLOCK_MONOTONIC, &t0);
    struct timespec warm_end; double warm = 1.0; int counting = 0; long b0 = 0, p0 = 0; struct timespec c0 = t0;
    struct epoll_event ev[256];
    for (;;) {
        int n = epoll_wait(ep, ev, 256, 100);
        for (int j = 0; j < n; j++) {
            unsigned long long d = ev[j].data.u64;
            if (d >= 1000000) {
                int i = d - 1000000;
                for (;;) {
                    int w = write(pub[i], buf + off[i], blen - off[i]);
                    if (w <= 0) break;
                    off[i] += w; if (off[i] == blen) { off[i] = 0; published += per; }
                }
            } else {
                for (;;) { int r = read(sub[d], rb, sizeof rb); if (r <= 0) break; bytes += r; }
            }
        }
        clock_gettime(CLOCK_MONOTONIC, &t);
        double el = (t.tv_sec - t0.tv_sec) + (t.tv_nsec - t0.tv_nsec) / 1e9;
        if (!counting && el >= warm) { counting = 1; b0 = bytes; p0 = published; c0 = t; }
        if (counting) {
            double ce = (t.tv_sec - c0.tv_sec) + (t.tv_nsec - c0.tv_nsec) / 1e9;
            if (ce >= secs) { printf("%.0f %.0f\n", (bytes - b0) / (double)PKT / ce, (published - p0) / ce); return 0; }
        }
    }
    (void)warm_end;
}
