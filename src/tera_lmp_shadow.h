/*
  Trace-only state isolation for the Terachess LMP offline harness.

  This header is inert unless TERA_LMP_TRACE is defined explicitly by an
  offline build. Production/OpenBench builds do not define that macro.
*/

#ifndef TERA_LMP_SHADOW_H_INCLUDED
#define TERA_LMP_SHADOW_H_INCLUDED

namespace Stockfish::TeraLmpShadow {

#ifdef TERA_LMP_TRACE
inline thread_local bool active = false;

class ScopedReadOnlySearch {
   public:
    ScopedReadOnlySearch() : previous(active) { active = true; }
    ~ScopedReadOnlySearch() { active = previous; }

    ScopedReadOnlySearch(const ScopedReadOnlySearch&)            = delete;
    ScopedReadOnlySearch& operator=(const ScopedReadOnlySearch&) = delete;

   private:
    bool previous;
};

inline bool suppress_writes() { return active; }
#else
inline constexpr bool suppress_writes() { return false; }
#endif

}  // namespace Stockfish::TeraLmpShadow

#endif  // TERA_LMP_SHADOW_H_INCLUDED
