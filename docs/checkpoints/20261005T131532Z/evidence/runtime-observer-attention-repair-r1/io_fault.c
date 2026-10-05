#define _GNU_SOURCE
#include <unistd.h>
#include <dlfcn.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
static int fail(int fd) {char link[64],path[4096];snprintf(link,sizeof(link),"/proc/self/fd/%d",fd);int n=readlink(link,path,sizeof(path)-1);if(n<0)return 0;path[n]=0;return strstr(path,"isolated-ioerr.db")!=NULL;}
ssize_t pwrite64(int fd,const void *buf,size_t n,off64_t at){if(fail(fd)){errno=EIO;return -1;}ssize_t(*real)(int,const void*,size_t,off64_t)=dlsym(RTLD_NEXT,"pwrite64");return real(fd,buf,n,at);}
ssize_t pwrite(int fd,const void *buf,size_t n,off_t at){if(fail(fd)){errno=EIO;return -1;}ssize_t(*real)(int,const void*,size_t,off_t)=dlsym(RTLD_NEXT,"pwrite");return real(fd,buf,n,at);}
