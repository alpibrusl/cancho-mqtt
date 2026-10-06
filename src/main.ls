edition 5;

// `mqtt` -- an MQTT 3.1.1 broker. Scaffold only (issue #2): it prints its name
// and exits. The design is `docs/design.md`; nothing here serves a client yet.

fn write_all[&r, &i](io: &!i Io, s: &r [byte]) -> [io_write] int {
    var n = 0;
    while n < len(s) {
        putchar(io, int_of(s[n]));
        n = n + 1;
    }
    return len(s);
}

fn main(world: World) -> [] int {
    let Split { io, ffi, fs, heap, args, net, clock } = split(world);
    release(args);
    release(heap);
    release(fs);
    release(ffi);
    release(net);
    release(clock);

    var written = 0;
    borrow mut io as &!i in {
        written = write_all(i, "lexsys-mqtt: scaffold, no broker yet\n");
    }
    release(io);
    if written > 0 {
        return 0;
    }
    return 1;
}
