#!/usr/bin/env bash
# This script crops a directory of images into many CSxCS crops
# Start with <CROPSIZE> run
# input:
#	args = CS DN
#	DN/{images.jpg}
# output:
#	DN-CS/images_n.jpg

# args
DN=$2	# dirname
CS=$1	# crop size
IMG=$3


if ! [[ "$CS" =~ ^[0-9]+$ ]] || ((CS%8!=0))
then
	echo "Syntax: bash $0 [CROPSIZE] [FILENAME] or bash $0 [CROPSIZE] run"
	echo "Error: ${CS} is an invalid crop size, CS must be a multiple of 8."
	exit -1
fi

if [ ! "$IMG" ]
then
    NTHREADS=$(grep -c ^processor /proc/cpuinfo)
    echo "Running with $NTHREADS threads..."
    ls ${DN} | xargs --max-procs=${NTHREADS} -n 1 bash $0 $1 $2
    exit
fi


echo "${IMG}"
RES=($(file ${DN}/${IMG} | grep -o -E '[0-9]{4,}x[0-9]{3,}' | grep -o -E '[0-9]+'))
BASENAME="${IMG%.*}"
ISO=$(grep -o 'ISOH*[0-9]*' <<< ${IMG})
if [ ! "${ISO}" ]
then
    ISO=UNK
fi
mkdir -p "${DN}_${CS}/${ISO}"

let CURY=CURX=CROPCNT=0
while (("$CURY"<${RES[1]}))
do
	CROPPATH="${DN}_${CS}/${ISO}/${BASENAME}_${CROPCNT}.jpg"
	if [ ! -f "${CROPPATH}" ]
	then
		#echo "jpegtran -crop ${CS}x${CS}+${CURX}+${CURY} -copy none -trim -optimize -outfile ${CROPPATH} ${DN}/${IMG}"
		jpegtran -crop ${CS}x${CS}+${CURX}+${CURY} -copy none -trim -optimize -outfile ${CROPPATH} ${DN}/${IMG}
	fi
	((CROPCNT++))
	((CURX+=CS))
	if ((CURX+CS>${RES[0]}))
	then
		CURX=0
		((CURY+=CS))
		if ((CURY+CS>${RES[1]}))
		then
			echo "${DN} cropped into $(((CROPCNT+1))) pieces."
		fi
	fi

done

